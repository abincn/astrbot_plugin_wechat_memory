"""Composition root. Features receive only the collaborators they use."""
from pathlib import Path
from .core.database import Database
from .features.access import Access
from .features.archive import Archive
from .features.identity import Identity
from .features.memory import Memory
from .features.analysis import Analysis
from .features.tasks import Tasks
from .features.usage import Usage
from .features.retrieval import Retrieval
from .features.transfer import Transfer
from .features.workbench import Workbench
from .features.privacy import Privacy


class Application:
    def __init__(self, directory, config, generate):
        self.directory = Path(directory)
        self.config = config
        self.db = Database(self.directory/'memory_vNext.sqlite3')
        self.archive = Archive(self.db)
        self.access = Access(self.db, config)
        self.identity = Identity(self.db)
        self.memory = Memory(self.db)
        self.usage = Usage(self.db, config)
        self.analysis = Analysis(self.archive, self.memory, self.identity, self.access, config, generate)
        self.tasks = Tasks(self.db, self.archive, self.access, self.memory, self.analysis, self.usage, config)
        self.retrieval = Retrieval(self.db, self.identity, self.access, config)
        self.transfer = Transfer(self.db, self.archive, self.memory, self.identity, config)
        self.workbench = Workbench(self.db)
        self.privacy = Privacy(self.db, self.archive, self.access, self.memory, self.identity, self.directory/'exports')

    async def close(self):
        await self.tasks.close()
        self.db.close()
