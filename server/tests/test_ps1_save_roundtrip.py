"""PS1 physical-card save: POST /ps1-save then GET /ps1-save round-trips."""

import os


def test_ps1_save_post_then_get(client, auth_headers):
    from app.services import ps1mc

    serial = "SLPS00555"
    name = "BISLPS-00555SOULEDGE"
    save = os.urandom(ps1mc.BLOCK_SIZE * 2)  # 2-block save

    up = client.post(
        f"/api/v1/saves/{serial}/ps1-save?name={name}",
        content=save,
        headers={**auth_headers, "Content-Type": "application/octet-stream"},
    )
    assert up.status_code == 200, up.text

    dl = client.get(f"/api/v1/saves/{serial}/ps1-save", headers=auth_headers)
    assert dl.status_code == 200, dl.text
    assert dl.headers["X-Save-Name"] == name

    import struct
    namelen = struct.unpack_from("<I", dl.content, 0)[0]
    got_name = dl.content[4:4 + namelen].decode()
    got_data = dl.content[4 + namelen:]
    assert got_name == name
    assert got_data == save
