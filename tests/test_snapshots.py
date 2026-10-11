from wechat_panel.snapshots import compare


def item(key, text='相同消息', direction='incoming'):
    return dict(key=key, text=text, direction=direction, kind='text')


def test_repeated_text_is_a_new_message_but_same_snapshot_is_not():
    first = [item('1')]
    assert compare(first, first).items == []
    assert compare(first, first + [item('2')]).items == [item('2')]


def test_visible_window_can_drop_older_messages():
    assert compare([item('1'), item('2')], [item('2'), item('3')]).items == [item('3')]


def test_recreated_window_reports_gap_without_replaying_tasks():
    delta = compare([item('old')], [item('new')])
    assert delta.gap and delta.historical


def test_first_snapshot_is_history_and_empty_snapshot_keeps_gap_visible():
    assert compare(None, [item('1')]).historical
    assert compare([item('1')], []).gap


def test_clipped_old_avatar_does_not_hide_a_new_incoming_message():
    previous = [item('1', direction='outgoing')]
    current = [item('1', direction='unknown'), item('2', text='新回复')]
    delta = compare(previous, current)
    assert delta.items == [current[1]]
    assert not delta.gap and not delta.historical
