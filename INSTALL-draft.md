# Installing STVID

This document is the authoritative installation guide for STVID. For the
project overview, configuration concepts, and normal operation, see
[README.md](README.md).

The commands below target Debian-family Linux systems, including Ubuntu and
Raspberry Pi OS. Package names may differ on other distributions.

## Requirements

- Linux on a desktop computer or single-board computer
- Python 3.9 or newer
- `git`, a C++ compiler, and `make`
- Internet access and an NTP-synchronised system clock
- A supported camera and any backend-specific SDK or system packages

STVID also calls four external programs during processing:

- `hough3dlines` for three-dimensional trail detection
- `satpredict` for satellite-position prediction
- Source Extractor, available to STVID as `sextractor`, for star detection
- Astrometry.net, including suitable index files, for plate solving

## 1. Install system packages

```bash
sudo apt update
sudo apt install git make g++ libeigen3-dev \
    python3 python3-dev python3-pip python3-venv \
    source-extractor astrometry.net wget
```

On distributions where the Source Extractor executable is named
`source-extractor`, expose the name expected by STVID:

```bash
sudo cp /usr/bin/source-extractor /usr/local/bin/sextractor
```

Confirm that it is available:

```bash
command -v sextractor
command -v solve-field
```

## 2. Install external astronomy tools

The following examples keep source checkouts under `$HOME/software` and install
the resulting executables in `/usr/local/bin`.

### hough3dlines

[hough3dlines](https://gitlab.com/pierros/hough3d-code) detects satellite
trails in image position and time.

```bash
mkdir -p $HOME/software
cd $HOME/software
git clone https://gitlab.com/pierros/hough3d-code.git
cd hough3d-code
make
sudo cp hough3dlines /usr/local/bin/
make test
```

### satpredict

[satpredict](https://github.com/cbassa/satpredict) calculates predicted
satellite positions from two-line element sets.

```bash
cd $HOME/software
git clone https://github.com/cbassa/satpredict.git
cd satpredict
make
sudo make install
```

### Astrometry.net index files

Astrometry.net requires index files appropriate to the camera's field of view.
The original wide-field STVID setup uses the 4100-series indexes (about 340 MB).
Check the `add_path` entries in `/etc/astrometry.cfg`; on many systems the index
directory is `/usr/share/astrometry`.

```bash
cd /tmp
for index in 4107 4108 4109 4110 4111 4112 4113 4114 4115 4116 4117 4118 4119; do
    wget -c "http://data.astrometry.net/4100/index-${index}.fits"
    sudo cp "index-${index}.fits" /usr/share/astrometry/
done
```

If your lens and sensor produce a different field of view, select indexes using
the guidance at [data.astrometry.net](http://data.astrometry.net/) instead.

## 3. Clone STVID

Skip the clone command if you are already working from a checkout.

```bash
cd $HOME/software
git clone https://github.com/cbassa/stvid.git
cd stvid
```

## 4. Install camera-specific system software

Install only the section required for your selected camera backend.

### OpenCV (`CV2`)

No vendor SDK is normally required. The base Python installation includes
`opencv-python`. The user running STVID must have permission to access the
camera device, commonly through membership of the `video` group.

### ZWO ASI (`ASI`)

Download the current Linux ASI Camera SDK from the
[ZWO product SDK page](https://www.zwoastro.com/software/product-sdk/), extract
it, and locate `libASICamera2.so` for your architecture. The path to that shared
library is configured as `sdk` in the `[ASI]` section of `configuration.ini`.

Installing and running ZWO's camera application can also install the required
`udev` rules and provides a useful independent camera and focusing test.

### Legacy Raspberry Pi camera (`PI`)

The `PI` backend uses the original Picamera interface and is intended for older
Raspberry Pi OS installations using the legacy camera stack. Use the operating
system packages appropriate to that legacy image. New Raspberry Pi
installations should normally use `PI2`.

### Picamera2 (`PI2`)

Picamera2 is supplied by Raspberry Pi OS and should be installed with `apt`,
not as a pip dependency:

```bash
sudo apt install -y python3-picamera2
```

Because Picamera2 is a system package, the STVID virtual environment must be
created with `--system-site-packages` in the next step.

### GenICam/GenTL (`GENTL`)

Install the camera manufacturer's GenTL Producer. It must provide a `.cti`
file compatible with the machine architecture. For example, an SVS-Vistek
installation may provide:

```text
/opt/SVS/SVCamKit/SDK/Linux64_x64/cti/libsv_gev_tl_x64.cti
```

Record the actual path for the `[GENTL]` `cti_file` setting. The Python
`gentl` extra installed below provides Harvester and the GenICam bindings; it
does not replace the vendor's producer.

## 5. Create the Python environment

For `CV2`, `ASI`, `PI`, or `GENTL` on a conventional Linux installation:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

For the Raspberry Pi `PI2` backend, inherit the apt-installed Picamera2 package:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Run the activation command again whenever you open a new shell:

```bash
source .venv/bin/activate
```

## 6. Install STVID

Install the repository as an editable Python package. Choose the command that
matches the required camera support:

```bash
python -m pip install -e .                 # Base install: CV2, PI, or PI2
python -m pip install -e ".[gentl]"       # Add GenICam/GenTL support
python -m pip install -e ".[zwo]"         # Add the ZWO Python binding
python -m pip install -e ".[all-cameras]" # Add GenTL and ZWO bindings
```

Only run one of these commands. The extras install Python bindings; the vendor
SDK or operating-system camera package from step 4 is still required.

`pip install -r requirements.txt` remains available for legacy environments,
but it installs the ZWO binding unconditionally and does not include GenTL
support. New installations should use the package commands above.

## 7. Create the configuration

```bash
cp configuration.ini-dist configuration.ini
```

Edit `configuration.ini` for the observer, paths, TLE credentials, camera
backend, and camera settings. In particular:

- Set `[Setup] camera_type` to `CV2`, `ASI`, `PI`, `PI2`, or `GENTL`.
- For ZWO, set `[ASI] sdk` to the installed `libASICamera2.so`.
- For GenTL, set `[GENTL] cti_file` to the vendor `.cti` file.
- Set `[Setup] observations_path` and `[Elements] tlepath` to writable
  directories.

The configuration template currently includes examples for the established
backends and GenTL. When using `PI2`, add a section such as:

```ini
[PI2]
device_id = 0
nx = 800
ny = 600
nframes = 100
framerate = 5.0
exposure_us = 200000
gain = 1.0
pixel_format = YUV420
buffer_count = 4
timeout_s = 2.0
timeout_retries = 2
```

Adjust the image size, exposure, gain, and frame rate for the camera and lens.
The current Picamera2 backend accepts `YUV420` and produces 8-bit luminance
frames for STVID.

See the configuration section of [README.md](README.md) for an explanation of
the main settings.

## 8. Verify the installation

Verify the Python package and external commands:

```bash
python -c "import stvid; print('STVID import OK')"
command -v hough3dlines
command -v satpredict
command -v sextractor
command -v solve-field
```

For GenTL:

```bash
python -c "from harvesters.core import Harvester; print('Harvester import OK')"
python tests/test_gentl_camera_rpi.py -c configuration.ini
```

The second command is a hardware integration test and requires the camera and
its GenTL Producer to be available.

For Picamera2:

```bash
python -c "from picamera2 import Picamera2; print('Picamera2 import OK')"
```

For ZWO:

```bash
python -c "import zwoasi; print('zwoasi import OK')"
```

## 9. Run STVID

Update the TLE catalogs before processing observations:

```bash
python update_tle.py -c configuration.ini
```

Use the original acquisition entry point for `PI`, `CV2`, and `ASI`:

```bash
python acquire.py -c configuration.ini
```

Use the common-camera acquisition entry point for `PI2` and `GENTL` (it also
supports the older backends):

```bash
python acquire_gentl.py -c configuration.ini
```

Process captured FITS files with:

```bash
python process.py -c configuration.ini
```
