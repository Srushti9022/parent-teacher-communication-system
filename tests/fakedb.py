"""TEST-ONLY stand-in for a MongoDB database, so the tests run without installing MongoDB.
It reads your real .bson dump files into memory and understands the small set of
PyMongo calls this app uses. Nothing here is used when you run the app itself."""
import copy
import datetime
import os
import re
import struct


# ---- tiny BSON reader (no dependencies) -------------------------------------------------
def _parse(b, i=0, as_array=False):
    size = struct.unpack_from("<i", b, i)[0]
    end, i = i + size, i + 4
    out = [] if as_array else {}
    while i < end - 1:
        kind = b[i]
        i += 1
        j = b.index(0, i)
        key, i = b[i:j].decode(), j + 1
        if kind == 1:
            value = struct.unpack_from("<d", b, i)[0]; i += 8
        elif kind == 2:
            n = struct.unpack_from("<i", b, i)[0]; value = b[i + 4:i + 4 + n - 1].decode("utf8"); i += 4 + n
        elif kind in (3, 4):
            value, i = _parse(b, i, kind == 4)
        elif kind == 5:
            n = struct.unpack_from("<i", b, i)[0]; value = bytes(b[i + 5:i + 5 + n]); i += 5 + n
        elif kind == 7:
            value = b[i:i + 12].hex(); i += 12
        elif kind == 8:
            value = bool(b[i]); i += 1
        elif kind == 9:
            ms = struct.unpack_from("<q", b, i)[0]; i += 8
            value = datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=ms)
        elif kind == 10:
            value = None
        elif kind == 16:
            value = struct.unpack_from("<i", b, i)[0]; i += 4
        elif kind == 18:
            value = struct.unpack_from("<q", b, i)[0]; i += 8
        else:
            raise ValueError("Unsupported BSON type %d" % kind)
        if as_array:
            out.append(value)
        else:
            out[key] = value
    return out, end


def read_bson(path):
    data, i, docs = open(path, "rb").read(), 0, []
    while i < len(data):
        doc, i = _parse(data, i)
        docs.append(doc)
    return docs


def load_dump(folder):
    return {f[:-5]: read_bson(os.path.join(folder, f)) for f in os.listdir(folder) if f.endswith(".bson")}


# ---- minimal PyMongo look-alike ---------------------------------------------------------
class DuplicateKeyError(Exception):
    pass


def _matches(doc, flt):
    for key, cond in flt.items():
        if key == "$or":
            if not any(_matches(doc, f) for f in cond):
                return False
        elif key == "$and":
            if not all(_matches(doc, f) for f in cond):
                return False
        else:
            value = doc.get(key)
            if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
                for op, arg in cond.items():
                    if op == "$in" and value not in arg: return False
                    if op == "$nin" and value in arg: return False
                    if op == "$ne" and value == arg: return False
                    if op == "$eq" and value != arg: return False
                    if op in ("$gt", "$gte", "$lt", "$lte"):
                        if value is None: return False
                        ok = {"$gt": value > arg, "$gte": value >= arg, "$lt": value < arg, "$lte": value <= arg}[op]
                        if not ok: return False
                    if op == "$regex":
                        flags = re.I if "i" in cond.get("$options", "") else 0
                        if not isinstance(value, str) or not re.search(arg, value, flags): return False
            elif value != cond:
                return False
    return True


def _project(doc, projection):
    if not projection:
        return doc
    if any(v for k, v in projection.items() if k != "_id"):
        return {k: v for k, v in doc.items() if k in projection and projection[k] or k == "_id"}
    return {k: v for k, v in doc.items() if projection.get(k, 1)}


class Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction=1):
        for k, d in reversed(key if isinstance(key, list) else [(key, direction)]):
            self.docs.sort(key=lambda x, k=k: (x.get(k) is None, x.get(k)), reverse=(d == -1))
        return self

    def skip(self, n):
        self.docs = self.docs[n:]
        return self

    def limit(self, n):
        self.docs = self.docs[:n] if n else self.docs
        return self

    def __iter__(self):
        return iter(self.docs)


class Result:
    def __init__(self, matched=0, modified=0, inserted_id=None):
        self.matched_count, self.modified_count, self.inserted_id = matched, modified, inserted_id


class Collection:
    def __init__(self, docs=None):
        self.docs, self.unique = docs or [], []

    def find(self, flt=None, projection=None):
        return Cursor([_project(copy.deepcopy(d), projection) for d in self.docs if _matches(d, flt or {})])

    def find_one(self, flt=None, projection=None):
        for d in self.docs:
            if _matches(d, flt or {}):
                return _project(copy.deepcopy(d), projection)
        return None

    def count_documents(self, flt):
        return sum(1 for d in self.docs if _matches(d, flt))

    def distinct(self, key, flt=None):
        return list({d[key] for d in self.docs if key in d and _matches(d, flt or {})})

    def create_index(self, keys, unique=False, **kw):
        if unique:
            self.unique.append(keys if isinstance(keys, str) else keys[0][0])

    def insert_one(self, doc):
        for key in self.unique:
            if any(d.get(key) == doc.get(key) for d in self.docs):
                raise DuplicateKeyError(key)
        doc = copy.deepcopy(doc)
        doc.setdefault("_id", "oid:%024x" % (len(self.docs) + 1))
        self.docs.append(doc)
        return Result(inserted_id=doc["_id"])

    def insert_many(self, docs):
        for d in docs:
            self.insert_one(d)
        return Result()

    def update_one(self, flt, update):
        for d in self.docs:
            if _matches(d, flt):
                before = copy.deepcopy(d)
                d.update(copy.deepcopy(update.get("$set", {})))
                return Result(1, 1 if d != before else 0)
        return Result(0, 0)


class FakeDB:
    def __init__(self, data):
        self.cols = {name: Collection(copy.deepcopy(docs)) for name, docs in data.items()}

    def __getitem__(self, name):
        return self.cols.setdefault(name, Collection())
