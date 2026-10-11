from io import BytesIO
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
import pytest

from wechat_panel.app import create_app
from wechat_panel.avatars import crop_avatar, row_avatar
from wechat_panel.bridge import BridgeError, WeixinBridge


def sample_frame():
    image = Image.new('RGB', (300, 150), '#fafafa')
    ImageDraw.Draw(image).rectangle((12, 32, 47, 67), fill='#234567')
    ImageDraw.Draw(image).rectangle((24, 32, 35, 67), fill='#dddd44')
    return image, (100, 200), 1


def test_avatar_crop_requires_fully_visible_identified_row_and_nonblank_image():
    frame = sample_frame()
    rect = SimpleNamespace(left=100, top=200, right=400, bottom=300)
    data = row_avatar(frame, rect, (100, 200, 400, 350), session=True)
    assert Image.open(BytesIO(data)).size == (64, 64)
    assert row_avatar(frame, rect, (100, 240, 400, 350), session=True) is None
    assert crop_avatar(frame, (100, 200, 136, 236)) is None
    assert crop_avatar(frame, (90, 200, 126, 236)) is None


@pytest.mark.parametrize('scale', [1, 1.25, 1.75, 2])
@pytest.mark.parametrize('outgoing',[False,True])
def test_message_avatar_has_full_symmetric_edges_at_different_display_scales(scale,outgoing):
    width, height = round(600*scale), round(100*scale)
    size = round(36*scale)
    x, y = width-round(22*scale)-size if outgoing else round(20*scale), round(8*scale)
    image = Image.new('RGB', (width, height), '#fafafa')
    draw = ImageDraw.Draw(image)
    draw.rectangle((x, y, x+size-1, y+size-1), fill='#234567')
    draw.rectangle((x+size//3, y, x+2*size//3, y+size-1), fill='#dddd44')
    origin = (100, 200)
    rect = SimpleNamespace(left=origin[0], top=origin[1], right=origin[0]+width, bottom=origin[1]+height)
    png = row_avatar((image, origin, scale), rect,
                     (rect.left, rect.top, rect.right, rect.bottom), outgoing=outgoing)
    avatar = Image.open(BytesIO(png)).convert('RGBA')
    # Both edges contain the same real avatar color, with no white strip.
    assert avatar.getpixel((0, 32)) == avatar.getpixel((63, 32)) == (35, 69, 103, 255)


def test_background_avatar_refresh_never_searches_and_explicit_sync_locates_missing_once():
    obj = WeixinBridge.__new__(WeixinBridge)
    obj.windows, obj.avatar_cache = {}, {}
    obj._check_account = lambda c: None
    obj._main_window = lambda: (_ for _ in ()).throw(RuntimeError('Main window is hidden'))
    obj._search_unique = lambda lookup: (_ for _ in ()).throw(AssertionError('Passive sync must not search'))
    contact = dict(id='one', lookup='Alice', account_id='self', wechat_id='alice')
    assert obj.avatars([contact]) == []
    data = row_avatar(sample_frame(), SimpleNamespace(left=100, top=200, right=400, bottom=300),
                      (100, 200, 400, 350), session=True)
    searches = []
    def search(lookup):
        searches.append(lookup)
        obj.search_avatar = (lookup, data)
    obj._search_unique = search
    assert obj.avatars([contact], locate=True)[0]['data'] == data
    obj.avatars([contact], locate=True)
    assert searches == ['Alice']
    obj._check_account = lambda c: (_ for _ in ()).throw(BridgeError('Wrong account'))
    assert obj.avatars([contact]) == []


def test_avatar_api_persistence_account_isolation_version_changes_and_delete(tmp_path):
    with TestClient(create_app(tmp_path, demo=True)) as client:
        store = client.app.state.store
        profile = dict(lookup='Alice', nickname='Alice', account_id='self', wechat_id='alice')
        contact = store.add_contact(profile)
        other = store.add_contact({**profile, 'account_id':'other'})
        cid = contact['id']
        assert client.get(f'/api/contacts/{cid}/avatar').status_code == 404
        image = row_avatar(sample_frame(), SimpleNamespace(left=100, top=200, right=400, bottom=300),
                           (100, 200, 400, 350), session=True)
        assert store.set_avatar('self', 'alice', image)
        cursor = store.latest_event()
        assert not store.set_avatar('self', 'alice', image)
        assert cursor == store.latest_event()
        before = store.contact(cid)['avatar_url']
        response = client.get(before)
        assert response.status_code == 200 and response.headers['content-type'] == 'image/png'
        assert response.content == image
        assert client.get(f"/api/contacts/{other['id']}/avatar").status_code == 404
        assert client.get(f'/api/contacts/{cid}/avatar?own=true').status_code == 404
        store.set_avatar('self', 'self', image)
        assert client.get(store.contact(cid)['self_avatar_url']).content == image
        alternate = Image.new('RGBA', (64, 64), '#567890')
        output = BytesIO()
        alternate.save(output, format='PNG')
        store.set_avatar('self', 'alice', output.getvalue())
        assert store.contact(cid)['avatar_url'] != before
        assert store.contacts()[0].get('avatar_url') or store.contacts()[1].get('avatar_url')
        with pytest.raises(ValueError):
            store.set_avatar('self', 'alice', b'a'*65537)
        store.delete_contact(cid)
        assert client.get(before).status_code == 404
        assert store.contact(other['id'])['avatar_url'] is None
