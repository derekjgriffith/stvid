# STVID

STVID is a collection of tools for the detection, astrometric reduction and
identification of satellites from video observations.

Originally developed for optical satellite observations using fixed cameras,
STVID supports several camera backends and can be adapted to a wide variety of
imaging systems.

## Features

- Continuous acquisition of image cubes
- Real-time FITS generation
- Satellite detection
- Astrometric calibration
- Identification using TLEs
- Compatible with SatNOGS Optical workflows

## Supported Camera Backends

STVID currently supports the following camera interfaces:

| Backend | Typical Hardware | Notes |
|---------|------------------|------|
| CV2 | USB webcams, industrial cameras with OpenCV support | Generic OpenCV interface |
| ASI | ZWO ASI cameras | Requires the ZWO SDK |
| PI2 | Raspberry Pi Camera Module | Uses libcamera/Picamera2 |
| GENTL | GenICam / GenTL compliant cameras | Tested with SVS-Vistek ECO814 |

The camera backend is selected in `configuration.ini`.

## Installation

See:

```
INSTALL.md
```

## Python Installation

STVID is now packaged as a standard Python project.

Typical installation:

```bash
python3 -m venv ~/venvs/stvid
source ~/venvs/stvid/bin/activate

python -m pip install -e .
```

For GenTL support:

```bash
python -m pip install -e ".[gentl]"
```

For ZWO support:

```bash
python -m pip install -e ".[zwo]"
```

To install all optional camera interfaces:

```bash
python -m pip install -e ".[all-cameras]"
```

## Raspberry Pi

When using Raspberry Pi OS with Picamera2:

```bash
python3 -m venv --system-site-packages ~/venvs/stvid
```

Install Picamera2 using apt:

```bash
sudo apt install python3-picamera2
```

Picamera2 is supplied by Raspberry Pi OS and is intentionally not installed by
pip.

## GenTL Cameras

GenTL cameras require a vendor-supplied GenTL Producer (.cti).

For example:

```
/opt/SVS/SVCamKit/SDK/Linux64_x64/cti/libsv_gev_tl_x64.cti
```

The CTI path is configured in `configuration.ini`.

## Configuration

The supplied `configuration.ini.example` provides an example configuration.

Typical parameters include:

- camera type
- image size
- exposure
- gain
- frame rate
- output directories

## Project Structure

```
stvid/
    camera/
        base.py
        cv2_camera.py
        asi_camera.py
        gentl_camera.py
        pi2_camera.py
```

New camera interfaces should derive from the common camera base class.

## License

See the project license.