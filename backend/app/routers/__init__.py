"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import hop, resource

# hop 路由必须排在 resource 之前：/api/hop/... 是固定路径，不能被 /api/{module} 抢先匹配。
ROUTERS = [hop, resource]
