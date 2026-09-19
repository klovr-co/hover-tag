# MFS Memory

Tag's Memory is MFS retrieval from sources the operator has indexed and
authorized, such as Slack history, repositories, docs, issues, databases, object
stores, or web crawls.

Tag does not maintain a second local note store. Add durable context as a real
MFS source, then include that source root in `MFS_ALLOWED_SCOPES`. The runtime
uses `mfs_search.py`, `mfs_ls.py`, and `mfs_cat.py` to retrieve only from those
allowed roots.
