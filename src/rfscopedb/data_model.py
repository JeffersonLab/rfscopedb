"""A package for interacting with data at a more tractable level"""

import json
from datetime import datetime
from typing import Optional, Dict, Tuple, Any, List

import mysql.connector
from mysql.connector.cursor import MySQLCursor
import numpy as np
import pandas as pd
from numpy import ndarray
from scipy.signal import periodogram

from .db import WaveformDB, QueryFilter
from .utils import get_datetime_as_utc


class Scan:
    """This class contains all the data from a scan of waveform data from one or more RF cavities and related logic.

    This class will store raw waveform data, generate collections of derivative data about each waveform, and hold
    additional data related to system state at the time of the scan.
    """

    def __init__(self, start: datetime, end: datetime, sid: Optional[int] = None):
        """Construct an instance and initialize data attributes

        Args:
            start: The date and time the scan was started
            end: The date and time the scan ended
            sid: The unique database scan ID for this object.  None implies that the object was not read from the
                 database.
        """

        self.id = sid
        self.start = start
        self.end = end

        # self.waveform_data will be structured as {
        #   {
        #     <cav_name1>: {<signal_name1>: [val1, val2, ... ], <signal_name2>: [val1, val2, ...]}, ...,
        #     <cav_name2>: {<signal_name1>: [val1, val2, ... ], <signal_name2>: [val1, val2, ...]}, ...,
        #     ...
        #   }
        # }
        self.waveform_data = {}

        # self.sampling_frequency will be structured
        # {
        #   <cav_name1>: sampling_freq1,
        #   <cav_name1>: sampling_freq2,
        #   ...
        # }
        self.sampling_rate = {}

        # self.analysis_scalar and self.analysis_array will be structure this where one will hold scalar values and the
        # other will hold np.array values
        #   {
        #     <cav_name1>: {<signal_name1>: {<metric1>: value, <metric2>: value}, <signal_name2>: {<metric1>...}},
        #     <cav_name2>: {<signal_name1>: {<metric1>: value, <metric2>: value}, <signal_name2>: {<metric1>...}},
        #     ...
        #   }
        self.analysis_scalar = {}
        self.analysis_array = {}

        self.scan_data_float = {}
        self.scan_data_str = {}

    def add_scan_data(self, float_data: Dict[str, float], str_data: Dict[str, str]) -> None:
        """Add data that applies to the entire scan and not a specific waveform.  There can be no overlap in keys.

        Args:
            float_data: A dictionary containing numeric data relating to the scan. Keys are data names (e.g. R1XXITOT)
            str_data: A dictionary containing textual data relating to the scan. Keys are data names. Useful for ENUMS.
        """

        for k in float_data.keys():
            if k in str_data.keys():
                raise ValueError(f"A metadata name may only appear once in either the float or str data. ('{k}')")

        self.scan_data_float.update(float_data)
        self.scan_data_str.update(str_data)

    def add_cavity_data(self, cavity: str, data: Dict[str, np.array], sampling_rate: float):
        """Add waveform data to this scan for a given cavity.  Analysis of the waveform values are done here.

        Args:
            cavity: The name of the cavity ("R123")
            data: Dictionary keyed on signal name ("Time", "GMES", etc.) with numpy arrays containing signal data
            sampling_rate: The sampling rate of the data given in Hertz (e.g. 5000 for 5 kHz).
        """
        self.waveform_data[cavity] = data
        self.analysis_scalar[cavity] = {}
        self.analysis_array[cavity] = {}
        self.sampling_rate[cavity] = sampling_rate

        for signal_name in data.keys():
            # Time is reflected in the sampling rate and can be ignored for analysis purposes
            if signal_name == "Time":
                continue

            scalars, arrays = self.analyze_signal(data[signal_name], sampling_rate=sampling_rate)
            self.analysis_scalar[cavity][signal_name] = scalars

            self.analysis_array[cavity][signal_name] = {}
            for arr_name, array in arrays.items():
                self.analysis_array[cavity][signal_name][arr_name] = array

    def insert_data(self, conn: mysql.connector.MySQLConnection):
        """Insert all data related to this Scan into the database

        Args:
            conn: Connection to the database
        """
        fmt = '%Y-%m-%d %H:%M:%S.%f'
        start_time = get_datetime_as_utc(self.start)
        end_time = get_datetime_as_utc(self.end)
        cursor = None
        try:
            # Transaction started by default since autocommit is off.
            cursor = conn.cursor()
            # Note: execute and executemany do sanitation and prepared statements internally.
            cursor.execute("INSERT INTO scan (scan_start_utc, scan_end_utc)  VALUES (%s, %s)",
                           (start_time.strftime(fmt), end_time.strftime(fmt)))
            cursor.execute("SELECT LAST_INSERT_ID()")
            sid = cursor.fetchone()[0]

            for cav, data in self.waveform_data.items():
                for signal_name in data:
                    if signal_name == "Time":
                        continue
                    wid = self._insert_waveform(cursor, sid, cav, signal_name)
                    self._insert_waveform_adata(cursor, wid, cav, signal_name)
                    self._insert_waveform_sdata(cursor, wid, cav, signal_name)

            self._insert_scan_fdata(cursor, sid)
            self._insert_scan_sdata(cursor, sid)

            # Commit the transaction if we were able to successfully insert all the data.  Otherwise, an exception
            # should have been raised that was caught to roll back the transaction.
            conn.commit()
        except (mysql.connector.Error, Exception) as e:
            if conn is not None:
                # There was a problem so this should roll back the entire transaction across all the tables.
                conn.rollback()
            if cursor is not None:
                cursor.close()

            raise e

    def _insert_waveform(self, cursor: MySQLCursor, sid: int, cav: str, signal_name: str) -> int:
        """Insert a waveform into the database and return it's wid key."""
        cursor.execute("INSERT INTO waveform(sid, cavity, signal_name, sample_rate_hz) VALUES (%s, %s, %s, %s)",
                       (sid, cav, signal_name, self.sampling_rate[cav]))
        cursor.execute("SELECT LAST_INSERT_ID()")
        return cursor.fetchone()[0]

    def _insert_waveform_adata(self, cursor: MySQLCursor, wid: int, cav: str, signal_name: str):
        """Insert the waveform array data to the database.

        Args:
            cursor: A database cursor
            wid: The unique id of the waveform
            cav: The name of the cavity ("R123")
            signal_name: The name of the signal ("GMES")
        """
        # Append the array data for the waveform.  'raw' is not an analytical waveform and needs to be done separately
        array_data = [(wid, "raw", json.dumps(self.waveform_data[cav][signal_name].tolist()))]
        for arr_name in self.analysis_array[cav][signal_name].keys():
            array_data.append(
                (wid, arr_name, json.dumps(self.analysis_array[cav][signal_name][arr_name].tolist())))

        cursor.executemany("INSERT INTO waveform_adata (wid, name, data) VALUES (%s, %s, %s)",
                           array_data)

    def _insert_waveform_sdata(self, cursor: MySQLCursor, wid: int, cav: str, signal_name: str):
        """Insert the waveform scalar data to the database.

        Args:
            cursor: A database cursor
            wid: The unique id of the waveform
            cav: The name of the cavity ("R123")
            signal_name: The name of the signal ("GMES")
        """

        data = []
        for metric_name, value in self.analysis_scalar[cav][signal_name].items():
            data.append((wid, metric_name, value))
        cursor.executemany("INSERT INTO waveform_sdata (wid, name, value) VALUES (%s, %s, %s)", data)

    def _insert_scan_fdata(self, cursor: MySQLCursor, sid: int):
        """Insert the float data associated with this scan into the database.

        Args:
            cursor: A database cursor
            sid: The unique database scan ID
        """
        data = []
        for key, value in self.scan_data_float.items():
            data.append((sid, key, value))

        if len(data) > 0:
            cursor.executemany("INSERT INTO scan_fdata (sid, name, value) VALUES (%s, %s, %s)", data)

    def _insert_scan_sdata(self, cursor: MySQLCursor, sid: int):
        """Insert the string data associated with this scan into the database.

        Args:
            cursor: A database cursor
            sid: The unique database scan ID
        """
        data = []
        for key, value in self.scan_data_str.items():
            data.append((sid, key, value))
        if len(data) > 0:
            cursor.executemany("INSERT INTO scan_sdata (sid, name, value) VALUES (%s, %s, %s)", data)

    @staticmethod
    def analyze_signal(arr, sampling_rate=5000) -> Tuple[dict, dict]:

        """Computes basic statistical metrics and power spectrum for a single waveform of length 8192 samples.

        Args:
            arr (np.array): the array containing data
            sampling_rate (float): samping frequency represented by data in Hz

        Returns:
            Tuple[dict, dict]: dictionary of scalar statistical metrics, dictionary of arrays data
                               (e.g. power spectrum array)
        """

        if not isinstance(arr, (list, np.ndarray, tuple)):
            raise TypeError(f"Input must be a list, numpy array, or tuple. Not {type(arr)}")

        arr = np.array(arr)

        if not np.issubdtype(arr.dtype, np.number):
            raise ValueError("Input array must contain only numerical values.")

        if len(arr) != 8192:
            raise ValueError(f"Input array must have exactly 8192 elements. Got {len(arr)} elements.")

        # basic statistics
        min_val = np.min(arr)
        max_val = np.max(arr)

        # power spectrum analysis using Welch's method
        f, pxx_den = periodogram(arr, sampling_rate)

        # noinspection PyUnresolvedReferences
        scalars = {
            "minimum": min_val,
            "maximum": max_val,
            "peak_to_peak": max_val - min_val,
            "mean": np.mean(arr),
            "median": np.median(arr),
            "standard_deviation": np.std(arr),
            "rms": np.sqrt(np.mean(np.square(arr))),
            "25th_quartile": np.percentile(arr, 25),
            "75th_quartile": np.percentile(arr, 75),
            "dominant_frequency": f[np.argmax(pxx_den)]
        }
        arrays: dict[str, ndarray] = {
            "power_spectrum": pxx_den
        }

        return scalars, arrays

    @staticmethod
    def row_to_scan(row: Dict[str, Any]) -> 'Scan':
        """Take a singe database row result and generates a Scan object from it.  Expects rows as dictionaries.

        Args:
            row: A dictionary result from a database cursor that contains basic information about a scan.

        Returns:
            A Scan object based on the database row result.
        """
        return Scan(start=row['scan_start_utc'].astimezone(), end=row['scan_end_utc'], sid=row['sid'])


class Query:
    """This class is responsible for running queries of waveform data against the database.

    The basic idea is that a Query will be staged, where information about the set of scans will be determined from the
    database.  The user can investigate the scan information to determine if they would like to continue querying data
    from the database.  Since querying large amounts of data from the database could consume large amounts of time and
    system resources, the user may wish to check how many scans are included in their query before continuing.
    """
    staged: bool
    scan_meta: None | pd.DataFrame
    wf_data: None | pd.DataFrame
    wf_meta: None | pd.DataFrame

    def __init__(self, db: WaveformDB, signal_names: List[str], *, array_names: Optional[List[str]] = None,
                 begin: Optional[datetime] = None, end: Optional[datetime] = None, scan_filter: QueryFilter = None,
                 wf_metric_names: Optional[List[str]] = None):
        """Construct a query object with the information needed to query scan and waveform data.

        Args:
            db: A WaveFromDB object that contains an active connection to the database.
            signal_names: A list of signals (a.k.a. waveforms) to query.  E.g., GMES, PMES, etc.
            array_names: Each signal/waveform may have multiple arrays of data associated with it.  For example, 'raw'
                         returns the unmodified waveform data, while 'power_spectrum' returns the power at different
                         frequencies.
            begin: The earliest start time for which a scan will be included in the query.  If None, there is
                   no earliest time filter.
            end: The latest end time for which a scan will be included in the query.  If None, there is latest time
                 filter.
             scan_filter: An object used to filter out scans based on metadata criteria.
             wf_metric_names: A list of scalar metrics related to a waveform that will be included if they exist in the
                              database.
            """

        self.db = db
        self.signal_names = signal_names
        self.array_names = array_names
        self.begin = begin
        self.end = end
        self.scan_filter = scan_filter
        self.wf_metric_names = wf_metric_names

        self.staged = False
        self.scan_meta = None
        self.wf_data = None
        self.wf_meta = None

    def stage(self):
        """Perform the initial query to determine which scans meet the requested criteria."""

        scan_rows = self.db.query_scan_rows(begin=self.begin, end=self.end, q_filter=self.scan_filter)
        self.scan_meta = pd.DataFrame(scan_rows, index=None)
        self.staged = True

    def get_scan_count(self):
        """Get the number of scans that meet the requested criteria."""
        return len(self.scan_meta)

    def run(self):
        """Run the full query that will return the full waveform data and metadata.  Must run stage() first."""
        if not self.staged:
            raise RuntimeError("Query not staged.")

        # Note that in the database, array names are specified by the "process" that generated them.
        rows = self.db.query_waveform_data(self.scan_meta.sid.values.tolist(), signal_names=self.signal_names,
                                           array_names=self.array_names)
        self.wf_data = pd.DataFrame(rows)

        rows = self.db.query_waveform_metadata(self.scan_meta.sid.values.tolist(), signal_names=self.signal_names,
                                               metric_names=self.wf_metric_names)
        self.wf_meta = pd.DataFrame(rows)

    @staticmethod
    def get_frequency_range(fs: float, n_samples: int):
        """Construct the frequency distribution of a periodogram or FFT given parameters of the initial signal.

        This distribution includes the nyquist frequency so is of length n_samples/2 + 1 to match scipy's periodogram
        method.

        Args:
            fs: The sampling frequency in Hertz
            n_samples: The number of samples in the original signal
        """

        # It is up to n_samples/2 + 1 since the frequency distribution includes zero, and scipy returns the nyquist
        # frequency fs/2 (many libraries seem to not).
        return np.array([i * float(fs) / n_samples for i in range(int(n_samples/2) + 1)])
