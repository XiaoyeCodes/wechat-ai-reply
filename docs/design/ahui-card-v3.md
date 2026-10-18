# 阿慧形象卡片 v3

日期：2026-10-17。

## 交付

- [人物原图](../../wechat_panel/static/agent-art/ahui-v3.png)：1024 × 1536。
- [形象卡片 PNG](../../output/ahui-character-card-v3.png)：2560 × 1520。
- [可编辑 HTML](../../output/ahui-character-card-v3.html)：奶油白、浅粉与灰绿配色，文字与肖像分别排版。

使用内置 image_gen 图像编辑：先以用户第一张图的中央人物作为身份、表情与姿势参考，第二张图仅作为服装参考，清理截图界面；随后根据新要求，仅轻微收窄脸颊两侧。卡片用本地 HTML/CSS 排版并经 Edge 导出，不再次生成或修改人物。

## 换装提示词

Use case: identity-preserve clothing transfer and screenshot cleanup. Image 1 is the PRIMARY EDIT TARGET: use ONLY the large central person, not the tiny inset people. Image 2 is CLOTHING REFERENCE ONLY. The user's highest priority is that the person in image 1 must remain the exact same person: preserve the face shape, jawline, chin, cheek contour, forehead, eyes and eyelid shapes, eye spacing, nose, lips, exact small closed-mouth smile, upward-looking open-eye expression, skin tone and texture, straight fringe, long black twin ponytails, head angle, shoulder angle, body proportions, standing pose, and both hand positions from image 1. Treat the complete head and face of image 1 as a locked photographic region. Do not redraw into another face, beautify, slim or round her face, enlarge eyes, change makeup, change age, alter expression, add cat ears, or turn the photograph into anime. Do NOT copy the downcast closed eyes, head tilt, hairstyle motion or outstretched arms of image 2. The ONLY change to the central person is the outfit: replace the navy blazer, dark red bow, white shirt, navy skirt and black socks with the exact outfit worn by the large central person in image 2: an open loose cream knit cardigan, pale blush pink button-front blouse with white pointed collar, soft dusty-pink bow, pink-and-cream plaid pleated skirt with matching pink waistband, and plain white knee-high socks. Keep the outfit's proportions and natural drape, rendered for image 1's unchanged pose and hands. Cleanly remove ALL social-media app UI, status bar, play triangle, floating icons, inset cutout figures, counters, borders, stickers, phone interface and text. Keep a simple unobtrusive light gray studio backdrop with matching original soft photographic light. Output one clean high-fidelity realistic photographic portrait, vertical 2:3, head through the knees/lower legs similar to the first central figure, showing the clothing and original pose. Frame with comfortable headroom. No extra people, objects, writing, watermark or logos. Preserve the original face and expression exactly; do not replace with a generic pretty face.

## 脸颊微调提示词

Perform a very subtle localized portrait retouch on this exact image. The user now explicitly requests only slightly slimmer sides of the face. Gently reduce the fullness along the outer left and right cheek contours by about 3-4 percent, symmetrically and naturally; softly taper the sides of the lower cheeks without creating a sharp V-shaped jaw or pointed chin. This is a tiny adjustment, not a face redesign or makeover. Preserve the SAME recognizable person, exact eye shapes and spacing, open upward gaze, nose, lips, small closed-mouth smile, chin shape and length, forehead, bangs, skin texture, makeup, hair and twin ponytails, head pose and size. Everything else must remain unchanged: cream knit cardigan, pink blouse, white collar, pink bow, pink plaid skirt, white socks, body proportions, shoulder and torso pose, both hands and finger placement, gray studio backdrop, light, shadows, and the full original vertical 2:3 framing. Do not slim the body, enlarge eyes, alter expression, change outfit, change hairstyle, change pose, smooth into a different face, or add any text or objects. High fidelity: modify only a narrow region along the cheek sides, retain the rest of the photograph as-is.

## 应用与验证

Agent 列表和联系人侧栏共用 `agent-art.js` 中的阿慧 v3 资产。保留现有 Agent 名称、绑定和提示词。已检查列表实际加载 v3 图片、侧栏同一映射下的图片加载与裁切、820px 宽度无横向溢出，以及页面无 JavaScript 异常和三个相关脚本语法检查。侧栏在独立预览节点中检查，未改动联系人绑定。
