"""Selection of image versions, independent of thumbnails and visible pages."""


def version_key(record):
    return record.path, record.size, record.modified_ns


class Selection:
    def __init__(self):
        self.available = {}
        self.keys = set()

    def replace_results(self, records):
        self.available = {version_key(record): record for record in records}
        self.keys.clear()

    def contains(self, record):
        return version_key(record) in self.keys

    def set_selected(self, record, selected):
        key = version_key(record)
        if key not in self.available:
            return
        if selected:
            self.keys.add(key)
        else:
            self.keys.discard(key)

    def select_all(self):
        self.keys.update(self.available)

    def clear(self):
        self.keys.clear()

    def selected_records(self):
        return [record for key, record in self.available.items() if key in self.keys]

    def __len__(self):
        return len(self.keys)
