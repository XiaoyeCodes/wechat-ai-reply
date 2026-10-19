"""Conservative UIA evidence for group identity and per-message attribution.

Text resembling ``Alice: ...`` is never sender evidence. Mention matching can
use a verified sender's independent visible body; it is not protocol metadata.
No chat-history export, OCR, or private-chat fallbacks.
"""
from collections import Counter
import hashlib
import json
import re
import time


class GroupEvidenceError(RuntimeError):
    pass


def group_key(account_id, lookup):
    if not account_id or not lookup:raise GroupEvidenceError('群绑定需要已核验的登录账号和完整群名')
    return 'group:' + hashlib.sha256((account_id + '\0' + lookup).encode('utf-8')).hexdigest()[:32]


def validate_group_profile(profile, lookup, account_id=None, members=None, self_name=''):
    if not profile.get('group_identity_verified') or profile.get('chat_type') != 'group':
        raise GroupEvidenceError('无法核验当前会话为群聊，已停止操作')
    if profile.get('lookup') != lookup or profile.get('nickname') != lookup:
        raise GroupEvidenceError('群聊完整名称与填写的信息不一致，已停止操作')
    actual = profile.get('account_id')
    if not actual or (account_id and actual != account_id):
        raise GroupEvidenceError('当前微信账号与群绑定账号不一致')
    if profile.get('wechat_id') != group_key(actual, lookup):
        raise GroupEvidenceError('群聊内部绑定标识不一致，请重新核验')
    if members and not profile.get('group_members_verified'):
        raise GroupEvidenceError('无法唯一核验指定群成员；请检查群内显示名是否完整、重名或尚未显示')
    if members and list(profile.get('group_members', [])) != list(members):
        raise GroupEvidenceError('指定群成员与核验结果不一致')
    if self_name and (not profile.get('group_self_name_verified') or profile.get('group_self_name') != self_name):
        raise GroupEvidenceError('无法核验本人在该群的显示名；请核对“我在本群的昵称”')
    return profile


def _value(control, name, default=''):
    try:
        value = getattr(control, name, default)
        return value() if callable(value) else value
    except Exception:return default


def _name(control):
    return str(_value(control, 'window_text', '') or '').strip()


def _auto_id(control):
    info = _value(control, 'element_info', None)
    return str(_value(info, 'automation_id', '') or '').lower()


def _class(control):
    return str(_value(control, 'class_name', '') or '').lower()


def _kind(control):
    return str(_value(_value(control, 'element_info', None), 'control_type', '') or '')


def _nodes(control):
    try:return list(control.descendants())
    except Exception:return []


def _visible(control):
    # Evidence outside the visible row is not attributed to this observation.
    return _value(control, 'is_visible', True) is not False


def _reference(control):
    """Exclude the quoted subtree, including nested body/sender/mention nodes."""
    current=control;seen=set()
    for _ in range(20):
        if current is None or id(current) in seen:return False
        seen.add(id(current))
        if any(tag in (_auto_id(current)+' '+_class(current)) for tag in ('quote','refer')):return True
        current=_value(current,'parent',None)
    return True  # A corrupt ancestry is not usable attribution evidence.


def _unique_controls(controls):
    result = [];seen = set()
    for control in controls:
        info = _value(control, 'element_info', None)
        rid = tuple(_value(info, 'runtime_id', ()) or ())
        key = ('runtime', rid) if rid else ('object', id(control))
        if key not in seen:seen.add(key);result.append(control)
    return result


def roster(panel, total=None):
    """Read the already-visible member grid only; never enumerate the group."""
    members = [n for n in _nodes(panel) if _class(n) == 'mmui::chatmembercell' and _visible(n)]
    names = [_name(n) for n in _unique_controls(members) if _name(n)]
    # Unique-name authorization is safe only with a complete exposed grid.
    complete = isinstance(total, int) and total > 0 and len(names) == total
    return names, complete


def group_info_panel(window):
    nodes = [n for n in _nodes(window) if _kind(n) == 'Group' and _visible(n)]
    panels = [n for n in nodes if _auto_id(n) in
              ('group_chat_info_view', 'chat_room_info_view', 'chatroom_info_view')]
    if not panels:
        # Weixin 4.1.15 exposes the member panel by class, with an empty ID.
        panels = [n for n in nodes if _class(n) == 'mmui::chatroommemberinfoview']
    panels = _unique_controls(panels)
    if len(panels) > 1:raise GroupEvidenceError('群信息面板不唯一，已停止核验')
    return panels[0] if panels else None


def member_list_roster(listing, total):
    # Qt's picker may expose every member without giving any row keyboard
    # focus. Empty section/separator cells are not members; duplicate names
    # remain separate so selected_members can reject ambiguous identities.
    rows = _unique_controls(listing.children(control_type='ListItem'))
    names = [_name(row) for row in rows if _visible(row) and _name(row)]
    return names, isinstance(total, int) and total > 0 and len(names) == total


def self_alias(panel):
    labels = {'我在本群的昵称', '我在群組的暱稱', 'My Alias in Group'}
    nodes = _nodes(panel)
    candidates = []
    for index, node in enumerate(nodes):
        if _name(node) not in labels:continue
        # The value must belong to that settings field, not a message bubble.
        parent = _value(node, 'parent', None)
        local = _nodes(parent) if parent is not None else []
        values = [_name(n) for n in local if _kind(n) in ('Text', 'Edit') and _name(n) not in labels and _name(n)]
        if len(set(values)) == 1:candidates.extend(values)
    values = set(candidates)
    return values.pop() if len(values) == 1 else ''


def selected_members(names, complete, requested):
    if not requested:return True
    counts = Counter(names)
    return complete and all(counts[name] == 1 for name in requested)


def observe_group_row(row, contact, direction, text, known_members=None):
    """Return per-row evidence, with body kept separate from sender identity.

Supported semantic nodes are explicit sender labels or named avatar controls,
and explicit mention entities (Hyperlink or a native Mention* control). A
    visible body can establish an @name match only after sender verification.
    """
    all_nodes = [n for n in _nodes(row) if _visible(n)]
    nodes = [n for n in all_nodes if not _reference(n)]
    senders = []
    for node in nodes:
        aid = _auto_id(node);cls = _class(node)
        sender_label = aid.rsplit('.', 1)[-1] in {'sender_name', 'sender_name_label', 'message_sender', 'chat_sender_name', 'sender_label'}
        avatar = any(tag in cls for tag in ('contactheadview', 'avatar', 'headimage')) and _kind(node) in ('Button', 'Image')
        if (sender_label and _kind(node) in ('Text', 'Button')) or avatar:
            value = _name(node)
            if value and value not in ('头像', '頭像', 'Avatar'):senders.append(value)
    names = set(senders)
    sender = names.pop() if len(names) == 1 else ''
    if known_members is not None and list(known_members).count(sender)!=1:sender = ''
    sender_verified = bool(sender and direction in ('incoming', 'outgoing'))
    body=text
    if sender_verified and body.startswith(sender+'\n'):body=body[len(sender)+1:]
    mentions = []
    for node in nodes:
        aid = _auto_id(node);cls = _class(node)
        native_mention = ('mention' in cls and _kind(node) in ('Hyperlink', 'Text', 'Button'))
        semantic_link = _kind(node) == 'Hyperlink' and aid.rsplit('.', 1)[-1] in {'mention', 'mention_entity', 'at_mention', 'mention_target'}
        if native_mention or semantic_link:
            name = _name(node)
            if name.startswith('@') and len(name) > 1:mentions.append(name[1:].strip())
    own = contact.get('group_self_name', '').strip()
    verified_own = bool(own and contact.get('group_self_name_verified', False))
    mention_me = bool(verified_own and sender_verified and own in mentions)
    mention_verified = bool(verified_own and sender_verified and mentions)
    mention_source='uia_entity' if mention_verified else ''
    # Native plain-text rows expose body independently from the sender avatar.
    # Reference/quote rows are excluded unless an independent body exists.
    body_nodes=[n for n in nodes if _auto_id(n).rsplit('.',1)[-1] in
        {'message_content','message_text','chat_text','body','text_content','message_body'}
        and _kind(n) in ('Text','Document')]
    body_names=list(dict.fromkeys(_name(n) for n in body_nodes if _name(n)))
    independent_body=body_names[0] if len(body_names)==1 else None
    raw_is_body=_class(row)=='mmui::chattextitemview' and not any(
        any(part in (_auto_id(n)+' '+_class(n)) for part in ('quote','refer')) for n in all_nodes)
    if not mention_verified and sender_verified and verified_own and (independent_body is not None or raw_is_body):
        if independent_body is not None:body=independent_body
        # Markdown-style quoted lines are also data, not a fresh @ request.
        visible='\n'.join(line for line in body.splitlines() if not line.lstrip().startswith(('>','＞')))
        visible=re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"', '', visible)
        pattern=r'(?<![\w@])@'+re.escape(own)+r'(?=$|[\s\u2005\u2009\u200b，。！？、,:;!?：；])'
        mention_me=bool(re.search(pattern,visible))
        mention_verified=True;mention_source='visible_text'
    identity = json.dumps([sender if sender_verified else '', bool(sender_verified), body], ensure_ascii=False)
    return {'sender':sender, 'sender_verified':sender_verified,
            'mention_me':mention_me, 'mention_verified':mention_verified,
            'mention_source':mention_source,'identity_text':identity,'text':body}


def group_total(bridge, window):
    label = window.child_window(**bridge.Texts.GroupLabelText)
    if not label.exists(timeout=.2):raise GroupEvidenceError('未找到群聊人数标签，无法核验群聊类型')
    raw = label.window_text().strip()
    match = re.fullmatch(r'[（(]?(\d+)[）)]?', raw)
    return int(match[1]) if match else None


def read_member_list(bridge, window, contact, total):
    """Read only the member tab of the explicitly chosen group's UI window.

Uses the same native member picker exposed by the bundled pyweixin library,
without exporting or traversing the message-history list behind that picker.
    """
    from pyweixin.Uielements import TabItems
    history=None;popup=None
    bridge._focus(window,contact)
    bridge._check_chat_type(window,contact)
    button=window.child_window(**bridge.Buttons.ChatHistoryButton)
    if not button.exists(timeout=.3):raise GroupEvidenceError('当前微信未暴露群成员列表入口')
    pid=window.process_id()
    try:
        bridge._click(button,contact,'ui.group.members.open')
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            def matching_title(value):
                escaped=re.escape(contact['lookup'])
                return bool(re.match(r'^(?:["“「]'+escaped+r'["”」]的|Chat History for "'+escaped+r'")',value))
            candidates=[bridge.desktop.window(handle=w.handle) for w in bridge.desktop.windows(
                class_name='mmui::SearchMsgUniqueChatWindow',visible_only=True) if w.process_id()==pid
                and matching_title(w.window_text())]
            if len(candidates)==1:history=candidates[0];break
            if len(candidates)>1:raise GroupEvidenceError('存在多个同名群成员窗口，已停止核验')
            time.sleep(.1)
        if history is None:raise GroupEvidenceError('无法唯一定位目标群的成员窗口')
        bridge._focus(history,contact)
        tab=history.child_window(**TabItems.GroupMembersTabItem)
        if not tab.exists(timeout=.5):raise GroupEvidenceError('当前微信版本未暴露群成员标签')
        bridge._click(tab,contact,'ui.group.members.tab')
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            popups=[bridge.desktop.window(handle=w.handle) for cls in ('Qt51514QWindowToolSaveBits','mmui::XPopover')
                for w in bridge.desktop.windows(class_name=cls,visible_only=True) if w.process_id()==pid]
            popups=_unique_controls(popups)
            if len(popups)==1:popup=popups[0];break
            if len(popups)>1:raise GroupEvidenceError('群成员选择窗口不唯一，已停止核验')
            time.sleep(.1)
        if popup is None:raise GroupEvidenceError('无法读取该群的成员选择列表')
        lists=popup.descendants(control_type='List')
        if len(lists)!=1:raise GroupEvidenceError('群成员列表不可唯一识别')
        listing=lists[0]
        bridge._record('ui.group.members.read',contact)
        names,complete=member_list_roster(listing,total)
        if complete:return names,True
        bridge._focus(popup,contact)
        listing.set_focus();listing.type_keys('{HOME}')
        entries=[];previous=None;deadline=time.monotonic()+40
        for _ in range(min((total or 5000)+20,5020)):
            if time.monotonic()>deadline:break
            bridge._check_account(contact)
            rows=[r for r in listing.children(control_type='ListItem') if r.has_keyboard_focus()]
            if len(rows)!=1:break
            row=rows[0];name=row.window_text().strip()
            signature=(tuple(row.element_info.runtime_id),name)
            if not name or signature==previous:break
            entries.append(name);previous=signature
            if total and len(entries)>=total:break
            listing.type_keys('{DOWN}');time.sleep(.02)
        return entries,bool(total and len(entries)==total)
    finally:
        if popup is not None and popup.exists(timeout=.1):popup.close()
        if history is not None and history.exists(timeout=.1):history.close()


def inspect_group(bridge, lookup, group_members=None, group_self_name='', account_id=None):
    from .bridge import BridgeError
    lookup=lookup.strip();requested=list(group_members or [])
    actual=bridge._ensure_account(account_id)
    target={'lookup':lookup,'nickname':lookup,'account_id':actual,'chat_type':'group',
            'wechat_id':group_key(actual,lookup)}
    # Search first even if a matching dialog exists: duplicate group/private
    # search results must never be silently resolved using a cached HWND.
    bridge._search_unique(lookup,chat_type='group')
    window=bridge._find_dialog(target)
    if window is None:window=bridge._open_dialog(target)
    if bridge._title(window)!=lookup or not bridge.Tools.is_group_chat(window):
        raise BridgeError('打开的不是完整名称匹配的目标群聊')
    bridge._check_account(target)
    total=group_total(bridge,window)
    names=[];complete=False;alias='';panel=None;opened=False
    button=window.child_window(**bridge.Buttons.ChatInfoButton)
    try:
        bridge._focus(window,target)
        # Native group settings contain ChatMemberCell controls just like the
        # existing direct-profile view. Their names are separate from messages.
        panel=group_info_panel(window)
        if panel is None:
            bridge._click(button,target,'ui.group.info.open');opened=True
            deadline=time.monotonic()+2
            while panel is None and time.monotonic()<deadline:
                time.sleep(.1)
                panel=group_info_panel(window)
        if panel is not None:
            names,complete=roster(panel,total)
            alias=self_alias(panel)
    finally:
        if opened:
            bridge._click(button,target,'ui.group.info.close')
    if not complete:names,complete=read_member_list(bridge,window,target,total)
    if not complete:raise BridgeError('群成员名单未能完整核验，请保持成员列表可访问后重试')
    if not selected_members(names,complete,requested):
        raise BridgeError('指定成员无法唯一核验：请使用完整群内显示名，重名时请先设置唯一群昵称')
    if alias and group_self_name and alias!=group_self_name:
        raise BridgeError('填写的本人群昵称与群信息显示不一致，请核对后重试')
    own=alias or group_self_name.strip()
    source='uia_settings' if alias else 'user_config' if own else ''
    profile=target|{'group_members':requested,'group_mention_only':True,'group_self_name':own,
        'group_self_name_source':source,'group_identity_verified':True,
        'group_members_verified':selected_members(names,complete,requested),
        'group_self_name_verified':bool(own),'group_member_count':total}
    validate_group_profile(profile,lookup,account_id,requested,group_self_name)
    bridge._check_account(target)
    if bridge._title(window)!=lookup or not bridge.Tools.is_group_chat(window):
        raise BridgeError('群信息核验期间会话发生变化，请重试')
    key=(actual,target['wechat_id'],lookup)
    bridge.profiles[key]=profile;bridge.verified_contacts.add(key)
    if not hasattr(bridge,'group_rosters'):bridge.group_rosters={}
    # None means there is no complete roster to constrain structural sender
    # observations. Never treat a partial grid as the whole member population.
    bridge.group_rosters[(actual,target['wechat_id'])]=names if complete else None
    bridge._record('ui.group.verified',target,members=len(requested),self_name_source=source)
    return profile
