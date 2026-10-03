from mufettis.rules.corpus_lookup import article_text_from_rag


class FakeCollection:
    def __init__(self):
        self.calls = []

    def get(self, where, include):
        self.calls.append(where)
        clauses = {k: v for c in where["$and"] for k, v in c.items()}
        if clauses.get("legislation_code") == "7.5.20767" and clauses.get("article_no") == "28":
            return {"documents": ["adi kefalet sayılır."]}
        return {"documents": []}


class FakeRag:
    def __init__(self):
        self.collection = FakeCollection()
        self.article_calls = 0

    def _article_chunks(self, law, article):
        self.article_calls += 1
        return [{"document": "MADDE 584- Eşlerden biri"}, {"document": "eşin rızası aranmaz."}] if (law, article) == ("6098", ("", "584")) else []


def test_law_number_and_code_lookup_with_cache():
    rag = FakeRag()
    lookup = article_text_from_rag(rag)
    assert lookup("6098", "584") == "MADDE 584- Eşlerden biri\neşin rızası aranmaz."
    assert lookup("6098", "584") and rag.article_calls == 1
    assert lookup("7.5.20767", "28") == "adi kefalet sayılır."
    assert lookup("6098", "999") is None and lookup("7.5.1", "1") is None
