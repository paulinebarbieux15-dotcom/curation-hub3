from app.urls import extract_urls, hamming, is_valid_url, normalize_url, simhash


def test_smart_paste_extracts_urls_from_free_text():
    text = ("Hi team, check https://www.example.com/news/article-1?utm_source=newsletter&id=4, "
            "and also www.site.org/page (great read). Short link: lnkd.in/abcDEF. "
            "Wikipedia: https://en.wikipedia.org/wiki/Drone_(disambiguation). Not a link: hello.world")
    urls = extract_urls(text)
    assert "https://www.example.com/news/article-1?utm_source=newsletter&id=4" in urls
    assert "https://www.site.org/page" in urls
    assert "https://lnkd.in/abcDEF" in urls
    assert "https://en.wikipedia.org/wiki/Drone_(disambiguation)" in urls
    assert len(urls) == 4


def test_normalization_removes_tracking_and_variants():
    a = normalize_url("https://www.Example.com/Article/?utm_source=x&utm_medium=y&id=3&fbclid=zz#top")
    b = normalize_url("http://example.com/Article?id=3")
    c = normalize_url("https://m.example.com//Article?id=3")
    assert a == b == c == "https://example.com/Article?id=3"


def test_validation():
    assert is_valid_url("https://example.com/a")
    assert not is_valid_url("not a url")
    assert not is_valid_url("ftp://example.com")


def test_simhash_detects_near_duplicates():
    base = " ".join(f"word{i}" for i in range(200))
    variant = base.replace("word150", "changed")
    other = " ".join(f"other{i}" for i in range(200))
    assert hamming(simhash(base), simhash(variant)) <= 3
    assert hamming(simhash(base), simhash(other)) > 10
    assert simhash("too short") == ""


def test_tweet_id_and_printable_tweet_page():
    from app.capture import tweet_html, tweet_id
    assert tweet_id("https://x.com/Airbus/status/1845123456789012345?s=20") == "1845123456789012345"
    assert tweet_id("https://twitter.com/a/statuses/42") == "42"
    assert tweet_id("https://x.com/Airbus") is None
    html = tweet_html({"text": "New <drone> battery", "user": {"name": "Airbus", "screen_name": "Airbus"},
                       "created_at": "2026-10-14", "mediaDetails": [{"media_url_https": "https://pbs.twimg.com/a.jpg"}]},
                      "https://x.com/Airbus/status/1")
    assert "New &lt;drone&gt; battery" in html and "pbs.twimg.com/a.jpg" in html and "@Airbus" in html
