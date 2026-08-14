from types import SimpleNamespace

from tools import list_gentl_cameras


class FakeHarvester:
    def __init__(self):
        self.files = []
        self.reset_called = False
        self.device_info_list = [
            SimpleNamespace(
                display_name="Camera A", vendor="Vendor", model="Model A",
                serial_number="123", id_="dev-a", user_defined_name="Roof",
                tl_type="GEV", version="1.0",
            ),
            SimpleNamespace(
                display_name="Camera B", vendor="Vendor", model="Model B",
                serial_number="456", id_="dev-b", user_defined_name="",
                tl_type="GEV", version="1.0",
            ),
        ]

    def add_file(self, path):
        self.files.append(path)

    def update(self):
        pass

    def reset(self):
        self.reset_called = True


def test_enumerate_devices(tmp_path):
    cti_file = tmp_path / "producer.cti"
    cti_file.touch()
    devices = list_gentl_cameras.enumerate_devices(cti_file, FakeHarvester)
    assert [device["serial_number"] for device in devices] == ["123", "456"]
    assert devices[0]["tl_type"] == "GEV"
    assert devices[1]["user_defined_name"] is None


def test_read_configuration_and_serial_selection(tmp_path):
    cti_file = tmp_path / "producer.cti"
    config = tmp_path / "configuration.ini"
    config.write_text(
        f"[gentl]\ncti_file = {cti_file}\ndevice_id = 0\nserial_number = 456\n",
        encoding="utf-8",
    )
    settings = list_gentl_cameras.read_configuration(config)
    devices = [{"index": 0, "serial_number": "123"},
               {"index": 1, "serial_number": "456"}]
    assert settings["cti_file"] == cti_file
    assert list_gentl_cameras.selected_index(devices, settings) == 1
