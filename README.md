# rfscopedb
Software for collecting, storing and accessing Scope Mode RF Waveforms.  This projects relies on python v3.11+.

## Documentation
More complete documentation can be found at [GitHub Pages](https://jeffersonlab.github.io/rfscopedb/).

## Quick Start Guide
Starting with version 0.2.0, source and wheel files are attached to each GitHub release and can be installed 
using pip as follows.  Update the version strings to match desired version.  See 
[Releases](https://github.com/JeffersonLab/rfscopedb/releases) for more details.
```
pip install https://github.com/JeffersonLab/rfscopedb/releases/download/v1.1.0/rfscopedb-1.1.0-py3-none-any.whl
```

### Example Usage
Below is an example of querying data from rfscopedb database.  This assumes you have a production database running
or that you have the provided docker container database running (see [Developer Quick Start Guide](#developer-quick-start-guide)).
```python
from rfscopedb.db import WaveformDB
from rfscopedb.data_model import Query

db = WaveformDB(host='localhost', user='scope_rw', password='password', array_names=['raw', 'power_spectrum'])

q = Query(db=db, signal_names=["GMES", "PMES"])
# queries information on the scans that meet the criteria in q.  This should be quick.
q.stage()
# queries the waveform data related to the scans found by stage().  This may take longer as each scan can have many
# waveforms, and each waveform is 8,192 samples long.
q.run()
print(q.wf_data.head())
```

## Developer Quick Start Guide
Download the repo, create a virtual environment using pythong 3.11+, and install the package in editable mode with 
development dependencies.  Then develop using your preferred IDE, etc.

*Linux*
```bash
git clone https://github.com/JeffersonLab/rfscopedb
cd rfscopedb
python3.11 -m venv venv
venv/bin/activate
pip install -e .[dev]
```

*Windows*
```bash
git clone https://github.com/JeffersonLab/rfscopedb
cd rfscopedb
\path\to\python3 -m venv venv
venv\Scripts\activate.ps1
pip install -e .[dev]
```

To start the provided database.
```
docker compose up
```

### Testing
This application supports testing using `pytest` and code coverage using `coverage`.  Configuration in `pyproject.toml`.
Integration tests required that the provided docker container(s) are running.  [Tests](https://github.com/JeffersonLab/rfscopedb/.github/workflows/test.yml) are automatically run on appropriate triggers.

| Test Type          | Command                                  |
|--------------------|------------------------------------------|
| Unit               | `pytest test/unit`                       |
| Integration        | `pytest test/integration`                |
| Unit & Integration | `pytest`                                 |
| Code Coverage      | `coverage run`                           |
| Linting            | `pylint src/ test/unit test/integration` |

### Documentation
Documentation is done in Sphinx and automatically built and published to GitHub Pages when triggering a new [release](https://github.com/JeffersonLab/rfscopedb/.github/workflows/release.yml).  To build documentation, run this commands from the project root.
```
sphinx-build -b html docsrc/source build/docs
```

### Release
Release are generated automatically when the VERSION file recieves a commit on the main branch.  Artifcates (packages) are not deployed to PyPI automatically as this is intended as a limited use application.  Build artifacts are automatically attached to the releases when generated along with the python dependency information for the build (requirements.txt).

## See Also
- [rfscopedb-container](https://github.com/JeffersonLab/rfscopedb-container)
