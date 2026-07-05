from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.agent.tool import FunctionTool

import asyncio
import sys
import subprocess
from pathlib import Path

# 获取当前插件文件所在的绝对路径，并加入 sys.path
thepath = str(Path(__file__).parent)
if thepath not in sys.path:
    sys.path.insert(0, thepath)

import fetcher


async def _run_cmd(cmd: str) -> str:
    process = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    stdout_str = stdout.decode('utf-8', errors='replace') if stdout else ''
    stderr_str = stderr.decode('utf-8', errors='replace') if stderr else ''
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, cmd, stdout_str, stderr_str)
    return stdout_str


@register("CSViewer", "Sug4rNYa", "一个简单的CS比赛数据查看工具", "1.0.4")
class CSViewer(Star):
    def __init__(self, context: Context):
        super().__init__(context)

        self.context.add_llm_tools()

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""

    # 注册指令的装饰器。指令名为 helloworld。注册成功后，发送 `/helloworld` 就会触发这个指令，并回复 `你好, {user_name}!`
    @filter.command("helloworld")
    async def helloworld(self, event: AstrMessageEvent):
        """这是一个 hello world 指令""" # 这是 handler 的描述，将会被解析方便用户了解插件内容。建议填写。
        user_name = event.get_sender_name()
        message_str = event.message_str # 用户发的纯文本消息字符串
        message_chain = event.get_messages() # 用户所发的消息的消息链 # from astrbot.api.message_components import *
        logger.info(message_chain)
        yield event.plain_result(f"Hello, {user_name}, 你发了 {message_str}!") # 发送一条纯文本消息


    @filter.llm_tool(name="CSViewer_get_Spotlight_Event")
    async def CSViewer_get_Spotlight_Event(self, event: AstrMessageEvent) -> MessageEventResult:
        '''
        查询当前正在进行的重要cs赛事。这个工具只能告诉你这个赛事的名字、id和奖金，如果要查询完整赛程，请使用get_Spotlight_Event_Matches。

        Args:

        '''
        resp = ''
        apis = fetcher.load_apis()
        client = fetcher.APIClient(apis, rate_limit=1.5) #TODO: 全部改成配置项
        spotlight_evt = fetcher.find_spotlight_event(client)
        if not spotlight_evt:
            #没有进行中重要赛事
            resp = "没有查询到正在进行中的赛事。"
        else:
            evt_id = spotlight_evt.get("eventId")
            prize = spotlight_evt.get("prize", "")
            name = spotlight_evt.get("nameZh") or spotlight_evt.get("name", "")
        
            resp = f"查询完成，当前进行的重要赛事是 {name}，赛事id是{evt_id}。"

        # yield event.plain_result(resp)
        return resp

    @filter.llm_tool(name="CSViewer_get_Spotlight_Event_Matches")
    async def CSViewer_get_Spotlight_Event_Matches(self, event: AstrMessageEvent) -> MessageEventResult:
        '''
        查询当前正在进行的重要cs赛事的完整赛程。需要运行一会儿。这会返回一个用制表符写就的带格式文本。

        Args:

        '''
        resp = ''
        apis = fetcher.load_apis()
        client = fetcher.APIClient(apis, rate_limit=1.5) #TODO: 全部改成配置项
        #查询重要赛事id
        spotlight_evt = fetcher.find_spotlight_event(client)
        
        # umo = event.unified_msg_origin
        # message_chain = MessageChain().message("⌈赛程查询工具正在运行，请稍等！⌋\n")
        # await self.context.send_message(event.unified_msg_origin, message_chain)


        if not spotlight_evt:
            #没有进行中重要赛事
            resp = "没有查询到正在进行中的赛事。"
        else:
            evt_id = spotlight_evt.get("eventId")
            
            #按id查询赛程

            fetcherpath = str(Path(__file__).parent/"fetcher.py")

            try:
                resp = await _run_cmd(f"python3 {fetcherpath} -e event -i {evt_id} -p")
            except subprocess.CalledProcessError as e:
                resp = f"查询失败，返回码：{e.returncode}\n错误输出：{e.stderr}"

        return resp

    @filter.llm_tool(name="CSViewer_get_Team_Recent_Matches")
    async def CSViewer_get_Team_Recent_Matches(self, event: AstrMessageEvent, team: str, count: int) -> MessageEventResult:
        '''
        查询给定队伍最近的若干场比赛。若指定比赛数量大于20，可能触发反爬，本工具将拒绝查询。

        Args:
            team(string): 要查询的队伍
            count(number): 要查询的场次数量

        '''
        if count > 20:
            return "查询场次数太多，工具拒绝查询。"

        fetcherpath = str(Path(__file__).parent/"fetcher.py")
        try:
            resp = await _run_cmd(f"python3 {fetcherpath} -e match --recent {count} --team {team} -p")
        except subprocess.CalledProcessError as e:
            resp = f"查询失败，返回码：{e.returncode}\n错误输出：{e.stderr}"

        return resp
    
    @filter.llm_tool(name="CSViewer_get_Team_Info")
    async def CSViewer_get_Team_Info(self, event: AstrMessageEvent, team: str) -> MessageEventResult:
        '''
        查询给定队伍的信息，包括排名、阵容等。输入格式为英文，如Tyloo, Navi, LVG, BC.G

        Args:
            team(string): 要查询的队伍

        '''
        if not team:
            return "查询队伍为空，无法查询。"

        fetcherpath = str(Path(__file__).parent/"fetcher.py")
        try:
            resp = await _run_cmd(f"python3 {fetcherpath} -e team --team \"{team}\" -p")
        except subprocess.CalledProcessError as e:
            resp = f"查询失败，返回码：{e.returncode}\n错误输出：{e.stderr}"

        return resp
    
    @filter.llm_tool(name="CSViewer_get_Match_Info")
    async def CSViewer_get_Match_Info(self, event: AstrMessageEvent, Id: int) -> MessageEventResult:
        '''
        查询给定比赛的信息。比赛id可以通过其他工具的查询得到。

        Args:
            Id(number): 要查询的比赛id

        '''
        
        fetcherpath = str(Path(__file__).parent/"fetcher.py")
        try:
            resp = await _run_cmd(f"python3 {fetcherpath} -e match -i {Id} -p")
        except subprocess.CalledProcessError as e:
            resp = f"查询失败，返回码：{e.returncode}\n错误输出：{e.stderr}"

        return resp
    
    @filter.llm_tool(name="CSViewer_get_Player_Info_By_Number_Id")
    async def CSViewer_get_Player_Info_By_Number_Id(self, event: AstrMessageEvent, Id: int) -> MessageEventResult:
        '''
        按选手数字id查询选手信息。

        Args:
            Id(number): 要查询的选手的数字id
        '''
        fetcherpath = str(Path(__file__).parent/"fetcher.py")
        try:
            resp = await _run_cmd(f"python3 {fetcherpath} -e player -i {Id} -p")
        except subprocess.CalledProcessError as e:
            resp = f"查询失败，返回码：{e.returncode}\n错误输出：{e.stderr}"

        return resp
    
    @filter.llm_tool(name="CSViewer_get_Player_Info_By_Ingame_Name")
    async def CSViewer_get_Player_Info_By_Ingame_Name(self, event: AstrMessageEvent, name: str) -> MessageEventResult:
        '''
        按选手游戏内名称查询选手信息。如果查询不到，请考虑选手是否花式拼写（leetspeak），如 monesy -> m0nesy, simple -> s1mple 这样。

        Args:
            name(string): 要查询的选手的游戏内名称
        '''
        fetcherpath = str(Path(__file__).parent/"fetcher.py")
        try:
            resp = await _run_cmd(f"python3 {fetcherpath} -e player --player-name {name} -p")
            
        except subprocess.CalledProcessError as e:
            resp = f"查询失败，返回码：{e.returncode}\n错误输出：{e.stderr}"
        


        return resp
    
    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""

    @filter.on_using_llm_tool()
    async def on_using_llm_tool(
        self,
        event: AstrMessageEvent,
        tool: FunctionTool, 
        tool_args: dict | None,
    ):
        # 告知用户调用工具需要等待
        if tool.name.startswith("CSViewer"):
            umo = event.unified_msg_origin
            message_chain = MessageChain().message("⌈正在调用CSViewer插件，查询费时，请稍等！⌋\n")
            await self.context.send_message(event.unified_msg_origin, message_chain)
