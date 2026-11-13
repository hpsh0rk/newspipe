"""True legacy adapter fixture: no partition interface and no external effects."""


def fetch(cfg=None, since=None):
    cfg = cfg or {}
    mode = cfg.get("mode", "plain")
    if mode == "integer_keys":
        title = cfg["weights"][1]
    elif mode == "yaml_date":
        title = cfg["start_date"].isoformat()
    elif mode == "yaml_types":
        title = "|".join((cfg["weights"][1], cfg["start_date"].isoformat(), cfg["stamp"].isoformat(),
                          cfg["blob"].hex(), ",".join(sorted(cfg["flags"]))))
    else:
        title = "Legacy"
    items = [{"ext_id": 123, "title": title, "pub_ts": since}]
    return iter(items) if cfg.get("generator") else items
