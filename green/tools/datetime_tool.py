from __future__ import annotations

from datetime import datetime

from ..services.tool_registry import Tool, ToolCategory


class GetDateTimeTool(Tool):
    name = "get_datetime"
    description = "Get the current local date, time, weekday and time zone of the user's computer."
    category = ToolCategory.READ

    def describe(self, args, prepared):
        return "Checking the current date and time"

    def run(self, args, prepared):
        now = datetime.now().astimezone()
        return {
            "local_datetime": now.isoformat(timespec="seconds"),
            "weekday": now.strftime("%A"),
            "timezone": now.tzname(),
            "utc_offset": now.strftime("%z"),
        }

    def summarize(self, data):
        return f"{data['weekday']} {data['local_datetime']}"
