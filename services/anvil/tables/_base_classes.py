class AppTables(object):
    def __repr__(self):
        return "<anvil.tables.{} object>".format(type(self).__name__)


class AbstractTableClass(object):
    __slots__ = ()
    _instead = None

    def __new__(cls, *args, **kwargs):
        raise TypeError(
            "Can't create a {} object. Use {} instead.".format(
                cls.__name__, cls._instead
            )
        )

    def __repr__(self):
        return "<anvil.tables.{} object>".format(type(self).__name__)

    def __dir__(self):
        # TODO should we keep this?
        # remove private attributes and methods from the dir
        return [
            key
            for key in object.__dir__(self)
            if (not key.startswith("_")) or key.startswith("__")
        ]


class Table(AbstractTableClass):
    _instead = "app_tables.my_table"


class SearchIterator(AbstractTableClass):
    _instead = "app_tables.my_table.search()"


class Row(AbstractTableClass):
    __slots__ = ()
    _instead = "app_tables.my_table.add_row()"
