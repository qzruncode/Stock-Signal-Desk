"""Explicit RSS source allowlist."""

RSS_OPERATIONS = [
    ("rss.gelonghui_subjects.get_subjects", "gelonghui_subjects", "get_subjects"),
    ("rss.nanhua_tree.get_nanhua_tree", "nanhua_tree", "get_nanhua_tree"),
    (
        "rss.cih_index_categories.get_cih_index_categories",
        "cih_index_categories",
        "get_cih_index_categories",
    ),
    ("rss.cls_subjects.get_cls_subjects", "cls_subjects", "get_cls_subjects"),
    ("rss.futunn_topics.get_futunn_topics", "futunn_topics", "get_futunn_topics"),
    ("rss.rss_namespace.get_namespaces_flat", "rss_namespace", "get_namespaces_flat"),
    ("rss.rss_namespace.get_namespace_detail", "rss_namespace", "get_namespace_detail"),
    ("rss.rss_namespace.get_categories", "rss_namespace", "get_categories"),
    ("rss.rss_catalog.get_rss_catalog", "rss_catalog", "get_rss_catalog"),
    ("rss.rss_catalog.list_catalog_routes", "rss_catalog", "list_catalog_routes"),
    ("rss.read_feed", "rss_reader", "read_feed"),
    ("rss.read_item", "rss_reader", "read_item"),
    ("rss.ui.get_rss_feeds_by_spec", "rss_views", "get_rss_feeds_by_spec"),
    ("rss.ui.get_rss_feed_item_detail", "rss_views", "get_rss_feed_item_detail"),
    ("rss.ui.get_rss_feeds_raw", "rss_views", "get_rss_feeds_raw"),
    ("rss.ui.transform_html", "rss_views", "transform_html"),
]
