"""Small, durable PostgreSQL identity store for the timetable.

The legacy credentials live inside a *large* JSON value in kv_store. Never
parse that value as PostgreSQL jsonb for a session check: doing so repeatedly
has previously killed database backends. On first access migrate users exactly
once by streaming the raw JSON over COPY BINARY. Future auth reads use only the
small auth_user_store row. No schedule data is modified by migration.
"""
import json
import struct
import time

STORE_KEY = 'schedule-data-v2'
# Stable PostgreSQL advisory key. Locks only *initial migration*, not logins.
_MIGRATION_LOCK = 715224319
_COPY_SQL = (
    "COPY (SELECT value_json FROM kv_store WHERE key = 'schedule-data-v2') "
    "TO STDOUT (FORMAT BINARY)"
)
_CREATE_SQL = (
    'CREATE TABLE IF NOT EXISTS auth_user_store ('
    'store_key TEXT PRIMARY KEY, users_json TEXT NOT NULL, '
    'updated_at DOUBLE PRECISION NOT NULL)'
)
_MAX_USERS_JSON = 2 * 1024 * 1024


class AuthDataError(RuntimeError):
    """Fail closed: unknown storage state must never permit setup to reset users."""


class _CopyBinaryField:
    """Read only the first PostgreSQL binary COPY field, in bounded chunks."""
    def __init__(self, copy):
        self.copy = copy
        self.pending = bytearray()
        signature = self._exact(11)
        if signature != b'PGCOPY\n\xff\r\n\0':
            raise AuthDataError('Unexpected PostgreSQL COPY format')
        flags, ext_length = struct.unpack('!II', self._exact(8))
        if flags & 0x00010000 or ext_length > 65536:
            raise AuthDataError('Unsupported PostgreSQL COPY header')
        if ext_length:
            self._exact(ext_length)
        fields = struct.unpack('!h', self._exact(2))[0]
        if fields != 1:
            raise AuthDataError('Legacy project row not found in kv_store')
        self.remaining = struct.unpack('!i', self._exact(4))[0]
        if self.remaining < 0:
            raise AuthDataError('Missing legacy project JSON')

    def _exact(self, length):
        while len(self.pending) < length:
            chunk = self.copy.read()
            if not chunk:
                raise AuthDataError('PostgreSQL COPY ended early')
            self.pending.extend(chunk)
        out = bytes(self.pending[:length])
        del self.pending[:length]
        return out

    def read(self, size=65536):
        if self.remaining <= 0:
            return b''
        size = min(max(1, size), self.remaining)
        result = self._exact(size)
        self.remaining -= len(result)
        return result

    def finish(self):
        """Finish the *entire* COPY OUT protocol before any subsequent SQL.

        Reading the field alone is not enough: PostgreSQL's binary COPY also
        sends a row terminator. Psycopg requires the COPY stream to be read
        through EOF; otherwise conn.execute() fails with ``another command is
        already in progress`` and the migration never commits.
        """
        while self.remaining:
            self.read(65536)
        if self._exact(2) != b'\xff\xff':
            raise AuthDataError('Unexpected PostgreSQL COPY row trailer')
        if self.pending:
            raise AuthDataError('Unexpected bytes after PostgreSQL COPY trailer')
        # This final EOF read is essential to release psycopg's COPY state.
        if self.copy.read():
            raise AuthDataError('Unexpected second PostgreSQL COPY row')


class _JSONByteReader:
    """Small lookahead reader; UTF-8 JSON delimiters are plain ASCII bytes."""
    def __init__(self, source):
        self.source = source
        self.buf = b''
        self.pos = 0

    def one(self):
        if self.pos >= len(self.buf):
            self.buf = self.source.read(32768)
            self.pos = 0
            if not self.buf:
                return None
        value = self.buf[self.pos]
        self.pos += 1
        return value


def _extract_users_from_stream(source):
    """Scan the top-level JSON object's keys and copy only its users array.

    The scanner tracks string escapes and nested braces; a text match such as
    '"users"' inside a timetable description is not considered a key. json.loads
    validates the extracted array before any users can be trusted.
    """
    src = _JSONByteReader(source)
    def next_nonspace():
        ch = src.one()
        while ch in (9, 10, 13, 32):
            ch = src.one()
        return ch

    if next_nonspace() != ord('{'):
        raise AuthDataError('Legacy project is not a JSON object; migration aborted')
    depth = 1
    expecting_key = True
    possible_user_key = False
    expecting_value = False
    quoted = False
    escaped = False
    key_bytes = None
    while (ch := src.one()) is not None:
        if quoted:
            if key_bytes is not None:
                if len(key_bytes) > 4096:
                    raise AuthDataError('Excessive JSON key length')
                key_bytes.append(ch)
            if escaped:
                escaped = False
            elif ch == ord('\\'):
                escaped = True
            elif ch == ord('"'):
                quoted = False
                if key_bytes is not None:
                    # Includes terminating quote; json parser supports escaped names.
                    try:
                        key = json.loads(bytes(key_bytes).decode('utf-8'))
                    except (UnicodeDecodeError, ValueError) as exc:
                        raise AuthDataError('Malformed JSON field name') from exc
                    possible_user_key = key == 'users'
                    expecting_value = False
                    key_bytes = None
            continue
        if ch == ord('"'):
            quoted = True
            if depth == 1 and expecting_key:
                key_bytes = bytearray(b'"')
                expecting_key = False
            continue
        if depth == 1 and ch == ord(':') and possible_user_key:
            expecting_value = True
            possible_user_key = False
            continue
        if expecting_value:
            if ch in (9, 10, 13, 32):
                continue
            if ch != ord('['):
                raise AuthDataError('Legacy users is not an array; migration aborted')
            # Array may contain nested objects, arrays and escaped quotes.
            captured = bytearray(b'[')
            nested = 1
            inside = False
            escaped_array = False
            while nested:
                b = src.one()
                if b is None:
                    raise AuthDataError('Incomplete users array in legacy project')
                captured.append(b)
                if len(captured) > _MAX_USERS_JSON:
                    raise AuthDataError('Users array exceeds migration safety limit')
                if inside:
                    if escaped_array:
                        escaped_array = False
                    elif b == ord('\\'):
                        escaped_array = True
                    elif b == ord('"'):
                        inside = False
                elif b == ord('"'):
                    inside = True
                elif b in (ord('['), ord('{')):
                    nested += 1
                elif b in (ord(']'), ord('}')):
                    nested -= 1
            try:
                users = json.loads(bytes(captured).decode('utf-8'))
            except (UnicodeDecodeError, ValueError) as exc:
                raise AuthDataError('Invalid users JSON in legacy project') from exc
            if not isinstance(users, list) or any(not isinstance(u, dict) for u in users):
                raise AuthDataError('Legacy users have an invalid format')
            return users
        if ch in (ord('['), ord('{')):
            depth += 1
        elif ch in (ord(']'), ord('}')):
            depth -= 1
            if depth < 0:
                raise AuthDataError('Malformed legacy JSON')
            if depth == 0:
                # A valid old project can legitimately have no users.
                return []
        elif depth == 1 and ch == ord(','):
            expecting_key = True
            possible_user_key = False
    raise AuthDataError('Incomplete legacy project JSON')


class _PrefixReader:
    def __init__(self, prefix, source):
        self.prefix = prefix
        self.source = source
    def read(self, size=65536):
        if self.prefix:
            part, self.prefix = self.prefix[:size], self.prefix[size:]
            return part
        return self.source.read(size)


def _legacy_users(conn):
    # COPY avoids creating a PostgreSQL jsonb copy of the entire timetable.
    with conn.cursor() as cur:
        with cur.copy(_COPY_SQL) as copy:
            field = _CopyBinaryField(copy)
            first = field.read(1)
            while first in (b' ', b'\t', b'\r', b'\n'):
                first = field.read(1)
            if first == b'"':
                # Pre-v1681 data sometimes stored JSON *as a JSON string*.
                # Bound the rare compatibility fallback to avoid allocating a
                # huge escaped timetable in server RAM. Never treat a failure
                # as an empty user list.
                if field.remaining > 4 * 1024 * 1024:
                    raise AuthDataError('Legacy double-encoded project exceeds safe migration limit')
                chunks = [first]
                while (chunk := field.read(65536)):
                    chunks.append(chunk)
                try:
                    project = json.loads(b''.join(chunks).decode('utf-8'))
                    project = json.loads(project) if isinstance(project, str) else project
                except (UnicodeDecodeError, ValueError) as exc:
                    raise AuthDataError('Invalid double-encoded legacy project') from exc
                if not isinstance(project, dict):
                    raise AuthDataError('Invalid legacy project root')
                users = project.get('users', [])
                if not isinstance(users, list) or any(not isinstance(u, dict) for u in users):
                    raise AuthDataError('Invalid legacy users')
                field.finish()
                return users
            if first != b'{':
                raise AuthDataError('Invalid legacy project root')
            result = _extract_users_from_stream(_PrefixReader(first, field))
            # Complete COPY including its binary trailer and final EOF read.
            field.finish()
            return result


def _decode_users(raw):
    try:
        users = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise AuthDataError('Corrupted authentication row') from exc
    if not isinstance(users, list) or any(not isinstance(u, dict) for u in users):
        raise AuthDataError('Invalid authentication list')
    return users


def _ensure_row(conn):
    """Initialize at most once; synchronize concurrent first requests via PG lock."""
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (_MIGRATION_LOCK,))
    if conn.execute("SELECT to_regclass('kv_store')").fetchone()[0] is None:
        raise AuthDataError('Legacy kv_store does not exist: wrong or empty database')
    conn.execute(_CREATE_SQL)
    row = conn.execute('SELECT users_json FROM auth_user_store WHERE store_key=%s', (STORE_KEY,)).fetchone()
    if row:
        return _decode_users(row[0])
    users = _legacy_users(conn)
    # Record an empty list only after a successful read of an existing JSON row.
    conn.execute('INSERT INTO auth_user_store(store_key,users_json,updated_at) VALUES(%s,%s,%s)',
                 (STORE_KEY, json.dumps(users, ensure_ascii=False, separators=(',', ':')), time.time()))
    return users


def read_users(database_url, connector):
    """Normal logins use a tiny table, never kv_store or JSONB."""
    with connector(database_url, connect_timeout=5) as conn:
        try:
            row = conn.execute('SELECT users_json FROM auth_user_store WHERE store_key=%s',
                               (STORE_KEY,)).fetchone()
        except Exception as exc:
            # Undefined table on a pristine deployment: no other SQL error
            # may trigger the migration (not a timeout, permission or OOM).
            if getattr(exc, 'sqlstate', None) != '42P01':
                raise
            # PostgreSQL marks a transaction failed on undefined_table;
            # rollback is required before CREATE TABLE and advisory lock.
            conn.rollback()
            return _ensure_row(conn)
        if row is not None:
            return _decode_users(row[0])
        return _ensure_row(conn)


def write_users(database_url, connector, users):
    """Update only credential row. Never serialize entire schedule for login."""
    if not isinstance(users, list) or any(not isinstance(u, dict) for u in users):
        raise AuthDataError('Invalid user list')
    with connector(database_url, connect_timeout=5) as conn:
        result = conn.execute(
            'UPDATE auth_user_store SET users_json=%s, updated_at=%s WHERE store_key=%s',
            (json.dumps(users, ensure_ascii=False, separators=(',', ':')), time.time(), STORE_KEY),
        )
        if result.rowcount != 1:
            raise AuthDataError('Authentication migration is not complete')


def write_users_in_transaction(conn, users):
    """Mirror explicit editor user changes atomically with project updates."""
    result = conn.execute(
        'UPDATE auth_user_store SET users_json=?, updated_at=? WHERE store_key=?',
        (json.dumps(users, ensure_ascii=False, separators=(',', ':')), time.time(), STORE_KEY),
    )
    if result.rowcount != 1:
        raise AuthDataError('Authentication storage row is missing')
