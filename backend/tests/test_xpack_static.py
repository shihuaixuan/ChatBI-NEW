from pathlib import Path

import sqlbot_xpack


def test_xpack_static_mount_serves_package_assets():
    import main

    static_dir = Path(sqlbot_xpack.__file__).resolve().parent / "static"
    matches = [
        route
        for route in main.app.routes
        if getattr(route, "path", None) == "/xpack_static"
        and getattr(route, "name", None) == "xpack_static"
    ]

    assert matches
    assert Path(matches[0].app.directory).resolve() == static_dir
