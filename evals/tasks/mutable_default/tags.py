class Article:
    def __init__(self, title, tags=[]):
        self.title = title
        self.tags = tags

    def add_tag(self, tag):
        tag = tag.strip().lower()
        if tag not in self.tags:
            self.tags.append(tag)
        return self
