# STVID

## Introduction
STVID (satellite tools for video) is a set of applications for observing the night sky with video cameras and detecting, measuring and identifying satellites in these observations.

![Example setup and results](img/example_setup.jpg  "Example setup and results")

STVID provides the following features:

- Automatic start and end of camera image data acquisition according to the sun elevation below the horizon
- Compression of raw video frames using the maximum temporal pixel method ([Gural & Segon 2009](https://ui.adsabs.harvard.edu/abs/2009JIMO...37...28G/abstract))
- Detection of satellites in position and time using the 3D Hough transform ([Dalitz et al. 2017](https://www.ipol.im/pub/art/2017/208/))
- Fast calculation of satellite predictions using different orbital catalogs of two-line elements (TLEs)
- Matching and identification of detected satellites against predictions
- Output results in [IOD format](http://www.satobs.org/position/IODformat.html) for publishing on [SeeSat-L](http://www.satobs.org/seesat/index.html)

## Table of Contents

1. [Requirements](#requirements)
1. [Installation](#installation)
1. [Configuration](#configuration)
1. [Operation](#operation)
1. [Supported camera backends](#supported-camera-backends)

## Requirements

- A desktop computer or single-board computer such as a Raspberry Pi, running Linux
- An internet connection to allow time synchronization using the Network Time Protocol (NTP)
- An analog video or digital CMOS camera. Currently supported cameras are:
	- Any camera working with OpenCV
	- ZWO ASI cameras
	- Raspberry Pi cameras through the legacy Picamera or current Picamera2 interface
	- GenICam/GenTL-compliant cameras (tested with an SVS-Vistek GigE Vision camera)
- A fast photographic lens, F/1.8 or faster, capable of delivering a pixel scale of 30 to 60 arsec/pix
- (optional) A weather proof CCTV housing


## Installation

Complete installation instructions are in [INSTALL.md](INSTALL.md). They cover
the required external astronomy tools, Python environment setup, and the
vendor or operating-system components needed by each camera backend.

For an existing system on which the external tools and camera dependencies are
already available, the Python package can be installed from the repository:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Use the `gentl`, `zwo`, or `all-cameras` optional dependency group when
required. Do not rely on this quick path for a new machine; follow
[INSTALL.md](INSTALL.md) instead.

## Configuration

<details>

STVID is configured through a configuration file. A boiler plate configuration file is included as `configuration.ini-dist`. Copy this file to `configuration.ini` using the following command

```bash
cp configuration.ini-dist configuration.ini          # Copy configuration file
```

Most parameters in `configuration.ini` do not need to be changed, except for the following:

#### Observer
- `cospar`: A COSPAR number if you have one, use a number between 9900 and 9999 otherwise.
- `name`, `latitude`, `longitude`, `height`: Your name and location (latitude, longitude, height) in the WGS84 coordinate frame.

#### Setup
- `camera_type`: Your camera selection: `ASI` for ZWO ASI cameras, `CV2` for
  OpenCV cameras, `PI` for the legacy Raspberry Pi camera interface, `PI2` for
  Picamera2, or `GENTL` for GenICam/GenTL cameras.
- `observations_path`: Directory where you want to store the observations.

#### Credentials
It is highly recommended to use the catalog of two-line elements (TLEs) from [space-track.org](https://www.space-track.org). Use the credentials of your account to download TLEs.

#### Elements
This section describes the TLE catalog that STVID downloads and how they are used and plotted.

- `tlepath`: Directory where you want to store the TLE catalogs

#### ZWO ASI cameras
For ZWO ASI cameras you need to specify the location of the ZWO ASI SDK libraries. For `x64` operating systems this is the `lib/x64/libASICamera2.so` shared library in the directory tree where you installed the SDK.

#### GenICam/GenTL cameras
GenTL cameras require the camera vendor's GenTL Producer (`.cti` file). Set
`cti_file` in the `[GENTL]` section to that file, then configure the device,
pixel format, exposure, frame rate, gain, image dimensions, and other camera
properties in the same section. The supplied `configuration.ini-dist` contains
an example based on an SVS-Vistek producer.

To list every camera exposed by the configured producer without opening a
camera or starting acquisition, run:

```bash
python tools/list_gentl_cameras.py -c configuration.ini
```

The camera selected by `serial_number` or `device_id` is marked with `*`.
Use `--json` when machine-readable discovery output is required.

#### Raspberry Pi cameras
Use the `[PI]` section with `camera_type = PI` for the legacy Picamera backend,
or the `[PI2]` section with `camera_type = PI2` for the libcamera/Picamera2
backend.

</details>

## Operation

<details>

There are three applications in STVID that work together:

- `update_tle.py` to download orbital catalogs of two-line elements (TLEs).
- `acquire.py` to capture data from your camera and store them as FITS files.
- `process.py` to analyse the FITS files and determine satellite positions.

The original `acquire.py` supports the `PI`, `CV2`, and `ASI` backends. Use
`acquire_gentl.py` for the newer common camera interface, including `PI2` and
`GENTL`:

```bash
python acquire_gentl.py -c configuration.ini
```

#### Updating TLEs

Assuming you have installed STVID in `$HOME/software/stvid` and your configuration is stored in `configuration.ini`, the TLE catalogs can be updated with the following command.
```bash
$HOME/software/stvid/update_tle.py -c $HOME/software/stvid/configuration.ini
```
This will download TLE catalogs from the following sources:

1. The master catalog called `catalog.tle` from [https://www.space-track.org](https://www.space-track.org). This requires your space-track.org credentials to be provided in `configuration.ini`.
1. The classified catalog `classfd.tle` from [Mike McCants](https://www.prismnet.com/~mmccants/tles/index.html). This catalog has TLEs for classified objects not present in `catalog.tle`.
1. The integrated elements `inttles.tle` from [Mike McCants](https://www.prismnet.com/~mmccants/tles/index.html). These are numerically integrated orbits converted into TLEs for objects at high altitudes.
1. Supplemental TLEs for Starlink satellites in `starlink.tle` from [celestrak.com](https://celestrak.org/NORAD/elements/supplemental/). These are TLEs computed from orbital ephemerides shared by the satellite operators and include predicted manouvers. These TLEs tend to be more accurate than those in `catalog.tle` which are based on observations.
1. Supplemental TLEs for OneWeb satellites in `oneweb.tle` from [celestrak.com](https://celestrak.org/NORAD/elements/supplemental/). These are TLEs computed from orbital ephemerides shared by the satellite operators and include predicted manouvers. These TLEs tend to be more accurate than those in `catalog.tle` which are based on observations.

</details>

## Supported camera backends

| Backend | Typical hardware | Notes |
| --- | --- | --- |
| `CV2` | USB webcams and other OpenCV-compatible cameras | Generic OpenCV interface |
| `ASI` | ZWO ASI cameras | Requires the ZWO SDK and the `zwo` extra |
| `PI` | Raspberry Pi cameras | Legacy Picamera interface for older Raspberry Pi OS releases |
| `PI2` | Raspberry Pi Camera Modules | Uses libcamera/Picamera2 supplied by Raspberry Pi OS |
| `GENTL` | GenICam/GenTL-compliant machine-vision cameras | Requires a vendor `.cti` producer and the `gentl` extra; tested with an SVS-Vistek GigE Vision camera |

## Todo

<details>

Features to be implemented.

#### High priority
* ~~Use sunset/sunrise times for starting/stopping data acquisition.~~
* ~~Automatic astrometric calibration.~~
* ~~Recognize unidentified satellite/meteor tracks using [3D Hough transform](http://www.ipol.im/pub/art/2017/208/).~~

#### Medium priority
* Pause data acquisition of the current line-of-sight (alt/az) is in the Earth's shadow for a particular orbital altitude.
* Investigate sensitivity loss of `significance=(max-mean)/sigma` if the four frame images are stored as 8bit integers instead of floats.


#### Low priority
* Implement python based star finding (stick with *source extractor* for now).
* Migrate to [python based SGP4/SDP4 algorithms](https://github.com/brandon-rhodes/python-sgp4)
* Use masks to mask unilluminated CCD areas.
* Investigate automatic submission of IOD measurements to [SeeSat-L](http://www.satobs.org/seesat/).
* ~~Migrate user settings to a configuration file.~~

## Run acquisition at startup

* Add user to video group (`sudo adduser <username> video`).
* Add video device to udev rules (add `SUBSYSTEM=="video1", GROUP="video", MODE="0660"` in `/etc/udev/rules.d/10-webcam.rules`).
* Create start up script in `/etc/init.d`. Call capture script as user with `su <username> -c "acquire.py"`.

</details>

## License
&copy; 2018-2023 Cees Bassa

Licensed under the [GPLv3](LICENSE).
