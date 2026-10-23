"""Sanitized, local recovery records. No SQL or credentials are recorded."""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import tempfile


@dataclass(frozen=True)
class RecoveryRecord:
    schema: str
    phase: str = 'not_started'
    outcome: str = 'no_writes'
    database: str = 'dao_eternity_test'
    role: str = 'dao_test_user'
    namespace_oid: int | None = None
    owner_oid: int | None = None
    inventory_digest: str | None = None
    table_oids: tuple = ()


class RecoveryJournal:
    def __init__(self, directory, record):
        if directory is None or not re.fullmatch(r'dao_it_[0-9a-f]{32}', record.schema):
            raise ValueError('An explicit recovery directory and generated schema are required.')
        self.directory = Path(directory).resolve(strict=True)
        if not self.directory.is_dir():
            raise ValueError('Recovery directory must already exist.')
        self.path = self.directory / (record.schema + '.json')
        self._last_bytes = self._encode(record)
        # Never reuse or overwrite a journal belonging to another run.
        with self.path.open('xb') as stream:
            stream.write(self._last_bytes)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _encode(record):
        return (json.dumps(asdict(record), sort_keys=True, indent=2) + '\n').encode()

    def save(self, record):
        if self.path.is_symlink() or self.path.read_bytes() != self._last_bytes:
            raise ValueError('Recovery journal changed; refusing overwrite.')
        data = self._encode(record)
        fd, temporary = tempfile.mkstemp(prefix=record.schema + '.', suffix='.tmp', dir=self.directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self._last_bytes = data
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
