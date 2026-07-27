# STVID Installation

## System Requirements

- Python 3.9 or newer
- pip
- git

Install system packages as required by your distribution.

On Debian / Raspberry Pi OS:

```bash
sudo apt update

sudo apt install \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    git
```

---

# Clone STVID

```bash
git clone https://github.com/cbassa/stvid.git

cd stvid
```

---

# Create a Python Environment

Standard Linux:

```bash
python3 -m venv ~/venvs/stvid

source ~/venvs/stvid/bin/activate
```

Raspberry Pi OS (Picamera2):

```bash
python3 -m venv --system-site-packages ~/venvs/stvid

source ~/venvs/stvid/bin/activate
```

---

# Install STVID

Upgrade pip:

```bash
python -m pip install --upgrade pip
```

Install STVID:

```bash
python -m pip install -e .
```

---

# Optional Camera Support

## GenTL

Install the vendor GenTL Producer.

Example (SVS-Vistek):

```
/opt/SVS/SVCamKit/SDK/Linux64_x64/cti/
```

Install Python support:

```bash
python -m pip install -e ".[gentl]"
```

Configure the CTI path in `configuration.ini`.

---

## ZWO ASI

Install the ZWO SDK.

Then:

```bash
python -m pip install -e ".[zwo]"
```

---

## Raspberry Pi Camera

Install:

```bash
sudo apt install python3-picamera2
```

No additional pip package is required.

---

# Legacy Installation

Previous versions of STVID used:

```bash
pip install -r requirements.txt
```

This remains available for compatibility.

New installations are encouraged to use:

```bash
python -m pip install -e .
```

which installs STVID as a standard editable Python package.

---

# External Software

STVID also requires several external applications.

These include:

- hough3dlines
- source-extractor
- astrometry.net
- satpredict

Refer to the existing documentation for installation of these packages.

---

# Verification

Confirm the installation:

```bash
python -c "import stvid"
```

If using GenTL:

```bash
python -c "from harvesters.core import Harvester"
```

If using Raspberry Pi:

```bash
python -c "from picamera2 import Picamera2"
```

---

# Running

Typical acquisition:

```bash
python acquire.py -c configuration.ini
```

GenTL:

```bash
python acquire_gentl.py -c configuration.ini
```

Processing:

```bash
python process.py -c configuration.ini
```