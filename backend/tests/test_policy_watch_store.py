from app.advisor.policy_watch import store as store_mod


class _Coll:
    def __init__(self):
        self.docs = []
        self._n = 0

    def find_one(self, q, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items() if not isinstance(v, dict)):
                return dict(d)
        return None

    def insert_one(self, doc):
        self._n += 1
        body = dict(doc)
        body.setdefault("_id", f"id{self._n}")
        self.docs.append(body)
        return type("R", (), {"inserted_id": body["_id"]})()

    def update_one(self, q, update, upsert=False):
        doc = self.find_one(q)
        if doc is None and upsert:
            doc = dict(q)
            self._n += 1
            doc.setdefault("_id", f"id{self._n}")
            self.docs.append(doc)
        if doc:
            real = next(
                d
                for d in self.docs
                if d.get("_id") == doc.get("_id") or d is doc
            )
            real.update(update.get("$set") or {})
            inc = update.get("$inc") or {}
            for k, v in inc.items():
                real[k] = int(real.get(k) or 0) + int(v)

    def find(self, q):
        out = []
        for d in self.docs:
            ok = True
            for k, v in q.items():
                if k == "source_key" and isinstance(v, dict) and "$in" in v:
                    if d.get("source_key") not in v["$in"]:
                        ok = False
                elif isinstance(v, dict) and "$in" in v:
                    if d.get(k) not in v["$in"]:
                        ok = False
                elif d.get(k) != v:
                    ok = False
            if ok:
                out.append(dict(d))
        return out


class _DB:
    def __init__(self):
        self.policy_watch_seen = _Coll()
        self.policy_watch_articles = _Coll()
        self.policy_watch_items = _Coll()
        self.policy_watch_source_scans = _Coll()


def test_seed_seen_idempotent(monkeypatch):
    db = _DB()
    monkeypatch.setattr(store_mod, "get_db", lambda: db)
    links = [{"url": "https://www.gov.cn/a.htm", "title": "新政"}]
    assert store_mod.seed_seen("gov_zhengce", links) == 1
    assert store_mod.seed_seen("gov_zhengce", links) == 0


def test_article_and_item_unique(monkeypatch):
    db = _DB()
    monkeypatch.setattr(store_mod, "get_db", lambda: db)
    a1 = store_mod.upsert_article(
        url="https://www.gov.cn/a.htm",
        title="新政",
        source_key="gov_zhengce",
        source_label="政府网",
        body_excerpt="正文",
        body_ok=True,
    )
    a2 = store_mod.upsert_article(
        url="https://www.gov.cn/a.htm?utm_source=x",
        title="新政",
        source_key="gov_zhengce",
        source_label="政府网",
        body_excerpt="正文2",
        body_ok=True,
    )
    assert a1["id"] == a2["id"]
    first = store_mod.insert_item("u1", a1["id"], "sent")
    assert first is not None
    assert store_mod.insert_item("u1", a1["id"], "sent") is None
    assert store_mod.list_unfanned_articles("u1", ["gov_zhengce"]) == []
    listed = store_mod.list_items("u1", filter="emailed", cursor=None, limit=30)
    assert len(listed["items"]) == 1
    assert listed["items"][0]["notify_status"] == "sent"
