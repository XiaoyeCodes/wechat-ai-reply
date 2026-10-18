# Agent 背景插画

生成方式：内置 image_gen；参考图生成后，以局部编辑修正发色。生成于 2026-10-17。

## 最终资产

- [甜心女仆](../../wechat_panel/static/agent-art/sweet-maid-v1.png)：巧克力棕头发、同色耳朵和尾巴，红茶与暖色茶室。
- [阿慧](../../wechat_panel/static/agent-art/ahui-v3.png)：用户指定的真人肖像，黑色双马尾与原表情、动作，米白开衫、粉色衬衫与格裙；脸颊两侧轻微收窄。[形象卡片与生成记录](ahui-card-v3.md)。
- [Hermes](../../wechat_panel/static/agent-art/hermes-v2.png)：独立文生图设计的银白桌面机器人，墨绿面罩、电脑和任务面板。
- [金融分析师](../../wechat_panel/static/agent-art/vibe-trading-v2.png)：用户指定的职业女性形象，柔和目光与自然微笑，黑色西装已去掉胸针，保留自然深棕发色和暖色书房。

甜心女仆使用猫耳角色作人物参考；阿慧 v3 改为用户提供的真人肖像与服装参考；Hermes 最终改为独立文生图的机器人；金融分析师使用另一张职业女性肖像作人物参考。所有资产复制到项目静态目录，不依赖临时路径。下方保留初版提示词，最新版本以对应修订记录为准。

列表卡片和联系人侧栏共用 agent-art.js 映射；换绑 Agent 时更新图片，解除绑定时清除旧图。装饰图片不读入屏幕阅读器，渐变遮罩保证正文可读。没有配置对应插画的 Agent 使用原有纯色卡片。

## 生成提示词

### sweet-maid

Use case: identity-preserve. Asset: decorative background for an Agent card in a light ivory and sage-green desktop app. Use the attached image as the character identity and illustration-style reference. Keep the recognizable face, violet-blue eyes, short dark navy hair with cyan inner streaks, dark cat ears with pale inner fur, and cat tail. Change costume, pose and scene as specified, maintaining the same appealing delicate anime illustration style. Wholesome nonsexual adult character, fully clothed. Output a wide landscape 2:1 image, ideally 2048x1024. Put the waist-up character on the RIGHT THIRD, complete head and ears visible with generous top margin. The LEFT 55 PERCENT must be quiet pale warm ivory with subtle scene shading, suitable for dark interface text; softly merge the scene into that negative space. Bottom fades into warm ivory. No typography, words, logos, watermark, UI or panel borders. Role: sweet maid Alice. Modest black long-sleeved maid dress, high white lace collar, white apron, a small white hair ornament between the cat ears. Holding a porcelain cup of red tea and a saucer, sweet confident smile with a hint of playful pride. Background: cozy manor tea corner, very subtle pastry plate and small black cat. Warm champagne and muted blush light, refined soft detail. Preserve the reference character's face and hair rather than replacing her with a different girl.

### ahui

Use case: identity-preserve. Asset: decorative background for an Agent card in a light ivory and sage-green desktop app. Use the attached image as the character identity and illustration-style reference. Keep the recognizable face, violet-blue eyes, short dark navy hair with cyan inner streaks, dark cat ears with pale inner fur, and cat tail. Change costume, pose and scene as specified, maintaining the same appealing delicate anime illustration style. Wholesome nonsexual adult character, fully clothed. Output a wide landscape 2:1 image, ideally 2048x1024. Put the waist-up character on the RIGHT THIRD, complete head and ears visible with generous top margin. The LEFT 55 PERCENT must be quiet pale warm ivory with subtle scene shading, suitable for dark interface text; softly merge the scene into that negative space. Bottom fades into warm ivory. No typography, words, logos, watermark, UI or panel borders. Role: Ahui everyday companion. Loose pale sage cardigan over a modest cream blouse, holding an iced latte with both hands near her waist, relaxed bright affectionate smile. Background: sunny small balcony, a pothos plant and distant orange-white cat suggested subtly. Gentle apricot sunlight and soft sage-green leaves. Keep her same cat ears, face, dark bob and cyan inner hair from the reference. Natural cozy daily life, no maid outfit.

### hermes

Use case: identity-preserve. Asset: decorative background for an Agent card in a light ivory and sage-green desktop app. Use the attached image as the character identity and illustration-style reference. Keep the recognizable face, violet-blue eyes, short dark navy hair with cyan inner streaks, dark cat ears with pale inner fur, and cat tail. Change costume, pose and scene as specified, maintaining the same appealing delicate anime illustration style. Wholesome nonsexual adult character, fully clothed. Output a wide landscape 2:1 image, ideally 2048x1024. Put the waist-up character on the RIGHT THIRD, complete head and ears visible with generous top margin. The LEFT 55 PERCENT must be quiet pale warm ivory with subtle scene shading, suitable for dark interface text; softly merge the scene into that negative space. Bottom fades into warm ivory. No typography, words, logos, watermark, UI or panel borders. Role: Hermes computer assistant. Stylish fully closed ivory tech jacket with restrained teal piping, small comfortable headset resting around neck without covering cat ears. At a modern laptop with one hand on trackpad, focused friendly confident expression. Subtle translucent browser-window shapes and organized file-folder shapes in the right background, no legible text. Pale ice-blue and sage office light, light futuristic details, no armor. Preserve the exact reference character identity, cat ears, short navy hair with cyan highlights and purple-blue eyes.

### vibe-trading

Use case: identity-preserve and background expansion. Asset: wide decorative background for the financial research Agent card in a light ivory and sage-green desktop app. Use the attached woman portrait as the exact identity and photographic-style reference. Preserve her recognizable face, dark brown side-swept bangs and low tied hair, small hoop earrings, black tailored blazer, beige collared shirt, brooch, arms-crossed confident professional pose and composed expression. Keep realistic photographic detail; do not turn her into anime or add cat ears. Expand/recompose into a wide 2:1 landscape image, ideally 2048x1024. Place the waist-up portrait entirely on the RIGHT THIRD, complete head visible with generous headroom. Warm sophisticated library/research office behind her with softly blurred books and a brass lamp, a subtle out-of-focus research chart on a desk. LEFT 55 PERCENT is a calm light warm ivory wall with a little very soft golden window shadow, clean low contrast negative space for dark UI text. Blend the dark warm portrait background smoothly toward the light left side; lower edge soft warm ivory. Polished editorial portrait, natural skin and lighting, no text, no letters, no logos, no watermark, no UI, no borders.

## 发色修正提示词

### sweet-maid

Edit this exact full-width illustration. ONLY correct the character's hair, cat-ear outer fur, and tail colors to suit the warm elegant maid persona and sunlit tea room. Replace ALL blue/cyan/navy hair coloring and the vivid cyan underside streaks with natural deep chocolate brown, subtle warm chestnut undertones and soft honey-colored highlights from the room's sunlight. The inner hair layer should be a harmonious slightly lighter warm brown, not blue, teal or gray. Match the cat-ear outer fur and tail to the chocolate-brown hair, keeping their pale inner fur. Preserve exactly her face, eyes and eye color, expression, hairstyle, maid clothes, teacup, black sleeping cat, pose, room details, lighting, negative space on left, complete composition and wide 2:1 aspect ratio. No new objects or text. This is a localized color correction; keep the original artwork detail and quality.

### ahui

Edit this exact full-width illustration. ONLY harmonize the character's hair, cat-ear outer fur and tail coloring with her relaxed everyday companion identity and the warm sunny balcony. Change the dark blue/navy hair and conspicuous cyan inner hair layer to a natural soft chestnut brown with delicate caramel highlights in the sunlight. The underside should be a natural warm medium brown, with no blue/cyan streaks. Match the outer cat-ear fur and tail to the chestnut brown hair while keeping the ears' pale inner fur. Preserve her recognizable face, violet-blue eyes, gentle expression, haircut, sage cardigan, cream blouse, iced drink, plants, orange cat, pose, sunlight, pale negative space on left, all existing details and the wide 2:1 composition. No other changes, no text.

## 验收

Edge 检查四张图片正常加载，列表与侧栏使用同一资产；四种形象依次切换、解除绑定无残留，2048px 与 820px 布局无横向溢出。仅修改静态界面与图片，不修改联系人绑定或角色系统提示词。
