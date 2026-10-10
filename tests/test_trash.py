from pipeline.trash import trash


def test_ad_and_deal_words_are_trash():
    for word in ["Продам", "срочно", "ТОРГ", "недорого", "самовывоз", "дёшево"]:
        assert trash(word)


def test_contacts_and_non_russian_are_trash():
    for word in ["https://avito.ru/1", "shop@mail.ru", "+79990001122", "ikbavfibyf", "42"]:
        assert trash(word)


def test_plausible_russian_words_are_not_trash():
    for word in ["Трактор", "шины", "ударом", "рабочие", "хлопок"]:
        assert not trash(word)
