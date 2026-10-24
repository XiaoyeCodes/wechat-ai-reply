"""FIFO admission for private conversation turns; no replay of old jobs."""

ACTIVE = ('pending', 'running', 'waiting_approval', 'draft', 'queued')


def position(store, run):
    with store.connect() as db:
        return db.execute("SELECT COUNT(*) FROM ai_runs older JOIN ai_runs current ON current.id=? "
            "WHERE older.contact_id=current.contact_id AND older.rowid<current.rowid "
            "AND older.status IN ('pending','running','waiting_approval','draft','queued')",
            (run['id'],)).fetchone()[0]


def is_head(store, run):
    """A turn owns the contact until its generation and delivery are settled."""
    return position(store,run)==0


def current(db, contact, run):
    """Later incoming messages are queued, rather than invalidating this turn."""
    source=db.execute('SELECT * FROM messages WHERE id=? AND contact_id=?',
                      (run['trigger_id'], contact['id'])).fetchone()
    return bool(source and source['id']>contact['ai_start_message']
        and source['direction']=='incoming' and not source['historical']
        and not source['text_pending'] and source['kind'] in ('text','image'))
