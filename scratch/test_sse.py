from sse_starlette.sse import ServerSentEvent
print(repr(str(ServerSentEvent(event="token", data="\n"))))
print(repr(str(ServerSentEvent(event="token", data="a\nb"))))
