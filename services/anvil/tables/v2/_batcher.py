import anvil
import anvil.server

from ._constants import NOT_FOUND, SERVER_PREFIX
from ._utils import ThreadLocal

PREFIX = SERVER_PREFIX + "row."
_make_refs = None  # Circular import


class _Batcher(ThreadLocal):
    _name = ""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = ThreadLocal.__new__(cls)
        return cls._instance

    def __init__(self):
        self._active = 0
        self._updates = []
        self._buffer = {}
        self._func = PREFIX + self._name

    @property
    def active(self):
        return self._active > 0

    def push(self, cap, update=False, on_behalf_of_client=False):
        self._updates.append((cap, update, on_behalf_of_client))

    def flush(self):
        if not self.active:
            return
        updates = self._updates
        if not updates:
            return
        try:
            anvil.server.call(self._func, self.get_args(updates))
            for cap, update, _ in updates:
                cap.send_update(update)
        finally:
            self.reset()

    def reset(self):
        self._updates.clear()
        self._buffer.clear()

    def __enter__(self):
        self._active += 1

    def get_args(self, updates):
        raise NotImplementedError

    def __exit__(self, exc_type, exc_value, traceback):
        is_final_context = self._active == 1
        try:
            if exc_value is None and is_final_context:
                self.flush()
        finally:
            self._active -= 1
            if is_final_context:
                self.reset()
            


class BatchUpdate(_Batcher):
    _name = "batch_update"

    def push(self, cap, update, on_behalf_of_client):
        self._updates.append((cap, update, on_behalf_of_client))
        self._buffer.setdefault(cap, {}).update(update)

    def get_updates(self, cap):
        return self._buffer.get(cap, {})

    def read(self, cap, key):
        return self.get_updates(cap).get(key, NOT_FOUND)

    def get_args(self, updates):
        global _make_refs
        if _make_refs is None:
            from ._refs import make_refs  # circular import

            _make_refs = make_refs

        return [
            (cap, _make_refs(update), on_behalf_of_client)
            for cap, update, on_behalf_of_client in updates
        ]


class BatchDelete(_Batcher):
    _name = "batch_delete_2"

    def get_args(self, updates):
        return [(cap, on_behalf_of_client) for cap, _, on_behalf_of_client in updates]


batch_update = BatchUpdate()
batch_delete = BatchDelete()


def flush():
    batch_update.flush()
    batch_delete.flush()


def flush_and_call(fn, *args, **kws):
    flush()
    return anvil.server.call(fn, *args, **kws)


class CombinedBatch(ThreadLocal):
    def __init__(self):
        self._batchers = [batch_delete, batch_update]

    def __enter__(self):
        for batcher in self._batchers:
            batcher.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        raise_exc = False
        for batcher in reversed(self._batchers):
            try:
                batcher.__exit__(exc_type, exc_value, traceback)
            except Exception as e:
                exc_type = type(e)
                exc_value = e
                raise_exc = True
        if raise_exc:
            raise exc_value


batch = CombinedBatch()
