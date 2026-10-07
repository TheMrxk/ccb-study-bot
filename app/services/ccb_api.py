"""建行学习业务接口封装：取课程 / 时长 / 进入记录 / 上报 / 校验。"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: int = 0, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class CcbClient:
    """带 token 的轻量客户端。token 由外部（runner）管理与刷新。"""

    def __init__(self, token: str, org_id: str = ""):
        self.token = token
        self.org_id = org_id
        self._client = httpx.AsyncClient(
            timeout=settings.request_timeout,
            headers={
                "Content-Type": "application/json",
                "token": token,
                "Origin": "https://u.ccb.com",
                "Referer": "https://u.ccb.com/",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, url: str, payload: dict) -> Any:
        resp = await self._client.post(url, json=payload)
        if resp.status_code == 401:
            raise ApiError("401 未授权（token 失效）", 401)
        if resp.status_code != 200:
            raise ApiError(
                f"HTTP {resp.status_code}", resp.status_code, resp.text[:500]
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ApiError("响应非 JSON", resp.status_code, resp.text[:500]) from exc
        # 平台常见成功标识 common_code == "OK" / code == 0，宽松校验并返回
        return data

    # ---- 接口 2：课程包下所有视频（真机为 GET，需 masterId） ----
    async def fetch_videos(self, package_id: str, master_id: str = "") -> list[dict]:
        url = (
            f"{settings.api_base}/v1/subknowledges/{package_id}"
            f"?masterId={master_id}&userstudyplanphaseid="
        )
        resp = await self._client.get(url)
        if resp.status_code == 401:
            raise ApiError("401 未授权（token 失效）", 401)
        if resp.status_code != 200:
            raise ApiError(f"HTTP {resp.status_code}", resp.status_code, resp.text[:500])
        data = resp.json()
        items = data.get("datas") or data.get("data") or []
        if isinstance(items, dict):
            items = items.get("datas") or []
        return items

    # ---- 课程详情：拿 sourceId(真 packageId) / masterId ----
    # ---- 独立视频初始化：拿 userKnowledgeId（记账钥匙） ----
    async def init_single_study(self, kid: str) -> dict:
        url = f"{settings.api_base}/v1/knowledge/{kid}"
        body = {"packageId": "", "planId": "", "readFlag": "1",
                "sourceType": "SingleStudy", "masterType": "",
                "masterID": "", "checkDelete": "1"}
        return await self._post(url, body)

    # ---- 学时首页：集中培训/网络自学已完成与目标 ----
    async def fetch_study_hours_home(self) -> dict:
        url = f"{settings.api_base}/v2/hf/userside/video4Hour/getUserStudyHistoryHomeV2"
        resp = await self._client.get(url)
        if resp.status_code == 401:
            raise ApiError("401 未授权（token 失效）", 401)
        if resp.status_code != 200:
            raise ApiError(f"HTTP {resp.status_code}", resp.status_code, resp.text[:500])
        d = resp.json()
        return d.get("data") or {}

    # ---- 训练营：全列表（POST，无参即可） ----
    async def fetch_camp_page(self) -> list[dict]:
        url = f"{settings.api_base}/v1/traincamp/userside/camp/selectCampPage"
        resp = await self._client.post(url, json={})
        if resp.status_code == 401:
            raise ApiError("401 未授权（token 失效）", 401)
        if resp.status_code != 200:
            raise ApiError(f"HTTP {resp.status_code}", resp.status_code, resp.text[:500])
        d = resp.json()
        return ((d.get("data") or {}).get("records")) or []

    # ---- 专题班：全列表（GET） ----
    async def fetch_workshop_home(self, limit: int = 50) -> list[dict]:
        url = (f"{settings.api_base}/v1/workshop/workshops/home"
               f"?limit={limit}&offset=0&orderby=praise&direction=DESC"
               f"&collegeId=&departmentId=&key=&tagId=&status=1")
        resp = await self._client.get(url)
        if resp.status_code == 401:
            raise ApiError("401 未授权（token 失效）", 401)
        if resp.status_code != 200:
            raise ApiError(f"HTTP {resp.status_code}", resp.status_code, resp.text[:500])
        d = resp.json()
        items = d.get("datas") or (d.get("data") or [])
        if isinstance(items, dict):
            items = items.get("datas") or []
        return items

    # ---- 直播间详情：场次列表 ----
    # ---- 专题班详情：contentList ----
    async def fetch_workshop_detail(self, workshop_id: str) -> dict:
        url = f"{settings.api_base}/v1/workshop/users/workshops/v2/{workshop_id}"
        resp = await self._client.get(url)
        if resp.status_code == 401:
            raise ApiError("401 未授权（token 失效）", 401)
        if resp.status_code != 200:
            raise ApiError(f"HTTP {resp.status_code}", resp.status_code, resp.text[:500])
        return resp.json()

    # ---- 专题班报名（workshop_id 在路径里，空body） ----
    async def register_workshop(self, workshop_id: str) -> dict:
        # 真实网页用 registerPOST（register 旧端点会抛HTTP500）
        url = (f"{settings.api_base}/v1/workshop/workshops/{workshop_id}/registerPOST"
               f"?channelId=")
        headers = {"source": "501",
                   "cparam1": "L3dvcmtzaG9wLyMvZGV0YWls",
                   "cparam2": "L3dvcmtzaG9wLyMvZGV0YWls"}
        resp = await self._client.post(url, headers=headers, content=b"")
        if resp.status_code == 401:
            raise ApiError("401 未授权（token 失效）", 401)
        if resp.status_code != 200:
            raise ApiError(f"HTTP {resp.status_code}", resp.status_code, resp.text[:500])
        return resp.json()

    async def fetch_course_detail(self, course_id: str) -> dict:
        url = f"{settings.api_base}/v1/knowledges/{course_id}?studyPlanId=&masterID=&masterType="
        resp = await self._client.get(url)
        if resp.status_code != 200:
            raise ApiError(f"HTTP {resp.status_code}", resp.status_code, resp.text[:500])
        return resp.json()

    # ---- 接口 3：视频播放信息（时长） ----
    async def fetch_video_info(self, payload: dict) -> dict:
        url = f"{settings.component_base}/v1/config/fetch"
        data = await self._post(url, payload)
        return data

    @staticmethod
    def extract_length_seconds(info: dict) -> float:
        try:
            items = info["newPlayListItem"]["videoItems"]
            if items:
                return float(items[0].get("videoLength") or 0)
        except (KeyError, TypeError, ValueError):
            pass
        return 0.0

    # ---- 接口 4：进入播放记录（真机 body） ----
    async def enter_study(
        self, knowledge_id: str, package_id: str, parent_id: str, title: str
    ) -> Any:
        url = f"{settings.api_base}/v1/hf/studyHistory/record"
        q = (f"knowledgeId={knowledge_id}&pKnowledgeId={parent_id}&packageId={package_id}")
        payload = {
            "h5Url": f"https://m.u.ccb.com/#/knowledges/redirectUrl?{q}",
            "pcUrl": f"https://u.ccb.com/JumpKng.aspx?{q}",
            "resourceClassified": "Video",
            "resourceId": knowledge_id,
            "resourceName": title,
            "resourceType": "0",
            "source": "501",
        }
        return await self._post(url, payload)

    # ---- 接口 5：上报进度 ----
    async def submit_schedule(
        self,
        knowledge_id: str,
        package_id: str,
        user_knowledge_id: str,
        master_id: str,
        view_schedule: float,
        time_span: float,
        times: int,
        point_list: list[int],
    ) -> Any:
        url = f"{settings.api_base}/v1/study/studyScheduleSubmit"
        payload = {
            "orgId": self.org_id,
            "knowledgeId": knowledge_id,
            "userKnowledgeId": user_knowledge_id,
            "masterId": master_id,
            "masterType": "",
            "packageId": package_id,
            "viewSchedule": round(float(view_schedule), 2),
            "workshopId": "",
            "channelId": "",
            "logChannelId": "",
            "moduleId": "",
            "Type": "1",
            "pointListMap": point_list,
            "times": times,
            "timeSpan": int(time_span),
            "version": 1,
        }
        return await self._post(url, payload)

    # ---- 接口 6：查询进度（真机 body） ----
    async def query_progress(self, knowledge_id: str, user_knowledge_id: str) -> dict:
        url = f"{settings.api_base}/v1/point/schedule/studypointview"
        payload = {
            "knowledgeId": knowledge_id,
            "userKnowledgeId": user_knowledge_id,
        }
        data = await self._post(url, payload)
        return data

    # ---- 个人真实进度（筛选未学/已完成用） ----
    async def fetch_knowledge_progress(self, kid: str) -> float | None:
        """微课/课程：初始化即返回 studyschedule(0-100)。

        返回进度百分比；取不到(异常)返回 None（视为未完成，保留显示）。
        """
        url = f"{settings.api_base}/v1/knowledge/{kid}"
        body = {"packageId": "", "planId": "", "readFlag": "1",
                "sourceType": "SingleStudy", "masterType": "",
                "masterID": "", "checkDelete": "1"}
        try:
            resp = await self._client.post(url, json=body)
            if resp.status_code != 200:
                return None
            j = resp.json()
            # 兼容平铺返回（顶层直接是 studyschedule）与 data/datas 包装
            d = j.get("data") or j.get("datas") or j
            sched = d.get("studyschedule")
            status = str(d.get("status") or "")
            if sched is None:
                return 100.0 if status == "Approved" else None
            return float(sched)
        except (ValueError, httpx.HTTPError):
            return None

    async def fetch_workshop_progress(self, workshop_id: str) -> float | None:
        """专题班：详情返回 progress(0-100)。未报名/无权限 HTTP400 → None。"""
        url = f"{settings.api_base}/v1/workshop/users/workshops/v2/{workshop_id}"
        try:
            resp = await self._client.get(url)
            if resp.status_code != 200:
                return None
            d = resp.json()
            data = d.get("data") or d
            p = data.get("progress")
            return float(p) if p is not None else None
        except (ValueError, httpx.HTTPError):
            return None
