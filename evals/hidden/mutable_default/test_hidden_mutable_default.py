from tags import Article


def test_many_articles_are_independent():
    articles = [Article(f"a{i}").add_tag(f"t{i}") for i in range(3)]
    assert [a.tags for a in articles] == [["t0"], ["t1"], ["t2"]]


def test_default_still_fresh_after_mutation():
    Article("first").add_tag("x")
    Article("second").tags.append("leak")
    assert Article("third").tags == []
