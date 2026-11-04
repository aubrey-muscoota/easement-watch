import numpy as np

from parcelwatch.detect import Settings, detect
from parcelwatch.run import default_windows
from datetime import date


def _grid(base=0.8, recent=0.8, n=5, size=40):
    b = np.full((size, size), base, "float32")
    r = np.full((size, size), recent, "float32")
    looks = np.full((size, size), n)
    parcel = np.zeros((size, size), bool)
    parcel[5:35, 5:35] = True
    return b, looks, r, looks.copy(), parcel


def test_no_change():
    res = detect(*_grid())
    assert res.status == "no_change" and res.loss_ha == 0


def test_clearing_inside_parcel_is_flagged():
    b, bn, r, rn, parcel = _grid()
    r[10:20, 10:20] = 0.2  # 100 px = 1 ha cleared
    res = detect(b, bn, r, rn, parcel)
    assert res.status == "alert"
    assert res.patches[0].area_ha == 1.0


def test_clearing_outside_parcel_is_ignored():
    b, bn, r, rn, parcel = _grid()
    r[0:5, 0:40] = 0.1
    assert detect(b, bn, r, rn, parcel).status == "no_change"


def test_small_patch_is_ignored():
    b, bn, r, rn, parcel = _grid()
    r[10:14, 10:14] = 0.1  # 0.16 ha, under the 0.25 ha default
    assert detect(b, bn, r, rn, parcel).status == "no_change"


def test_already_bare_land_is_not_flagged():
    b, bn, r, rn, parcel = _grid(base=0.3, recent=0.05)
    assert detect(b, bn, r, rn, parcel).status == "no_change"


def test_cloudy_parcel_reports_no_data():
    b, bn, r, rn, parcel = _grid(n=1)
    r[10:20, 10:20] = 0.1
    assert detect(b, bn, r, rn, parcel, Settings()).status == "no_data"


def test_default_windows_compare_same_season():
    base, recent = default_windows(date(2026, 11, 4))
    assert recent == (date(2026, 8, 6), date(2026, 11, 4))
    assert base == (date(2025, 8, 6), date(2025, 11, 4))


def test_default_windows_handle_leap_day():
    base, recent = default_windows(date(2028, 2, 29), days=10)
    assert base[1] == date(2027, 2, 28)
