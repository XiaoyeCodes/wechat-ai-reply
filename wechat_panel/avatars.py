"""Small local avatar images, cropped only from identified Weixin controls."""
from io import BytesIO

from PIL import Image, ImageDraw, ImageStat


def crop_avatar(frame, bounds, visible_bounds=None):
    image, (left, top), scale = frame
    x, y, right, bottom = (round(v) for v in bounds)
    visible = visible_bounds or (left, top, left+image.width, top+image.height)
    if (x < max(left, visible[0]) or y < max(top, visible[1])
            or right > min(left+image.width, visible[2])
            or bottom > min(top+image.height, visible[3])
            or min(right-x, bottom-y) < 24 or max(right-x, bottom-y) > 512
            or abs((right-x)-(bottom-y)) > 3):
        return None
    crop = image.crop((x-left, y-top, right-left, bottom-top)).convert('RGB')
    # Blank or failed PrintWindow renders must never replace a cached avatar.
    center = crop.crop((4, 4, crop.width-4, crop.height-4))
    if max(ImageStat.Stat(center).stddev) < 8:
        return None
    crop = crop.resize((64, 64), Image.Resampling.LANCZOS).convert('RGBA')
    mask = Image.new('L', (64, 64))
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 63, 63), radius=9, fill=255)
    crop.putalpha(mask)
    output = BytesIO()
    crop.save(output, format='PNG')
    return output.getvalue()


def row_avatar(frame, rect, visible_bounds, outgoing=False, session=False):
    """Weixin 4's session and message rows have a 36 DIP avatar."""
    scale = frame[2]
    size = round(36*scale)
    # Outgoing bubbles reserve another 4 DIP for the right scroll gutter.
    # UIA row bounds include that gutter; using the left-side margin here
    # clips the avatar's left edge and includes background along its right.
    margin = round((12 if session else 22 if outgoing else 20)*scale)
    x = rect.right-margin-size if outgoing else rect.left+margin
    y = round((rect.top+rect.bottom-size)/2) if session else rect.top+round(8*scale)
    return crop_avatar(frame, (x, y, x+size, y+size), visible_bounds)
