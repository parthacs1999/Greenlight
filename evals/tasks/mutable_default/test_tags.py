from tags import Article


def test_add_tag_normalises():
    a = Article("Hello").add_tag("  Python ")
    assert a.tags == ["python"]


def test_articles_do_not_share_tags():
    a = Article("One").add_tag("ml")
    b = Article("Two")
    assert b.tags == []
    assert a.tags == ["ml"]


def test_no_duplicates():
    a = Article("X").add_tag("ai").add_tag("AI")
    assert a.tags == ["ai"]


def test_caller_list_not_mutated():
    initial = ["news"]
    a = Article("Y", tags=initial).add_tag("tech")
    assert initial == ["news"]
    assert a.tags == ["news", "tech"]
