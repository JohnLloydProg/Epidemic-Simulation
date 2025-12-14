AGENT_GO_WORK = 0
AGENT_TRAVERSE = 1
AGENT_GO_HOME = 2
AGENT_INFECTED = 3
AGENT_REMOVED = 4
_events:dict[int, list['Event']] = {}


class Event:
    def __init__(self, type:int, agents:list):
        self.type = type
        self.agents = agents
    
    def __str__(self) -> str:
        return f"Event(type={self.type}, agents={self.agents})"


def get(time:int) -> list[Event]:
    return _events.pop(time, [])

def emit(target_time:int, event_type:int, agent):
    target_time += target_time % 2
    if (target_time in _events):
        events_in_time = _events.get(target_time)
        for event in events_in_time:
            if (event.type == event_type):
                event.agents.append(agent)
                return
        events_in_time.append(Event(event_type, [agent]))
    else:
        _events[target_time] = [Event(event_type, [agent])]

if __name__ == '__main__':
    from graphing.mapping import load_graph
    from graphing.graph import Graph
    from agents.agent import Agent
    graph:Graph = load_graph()
    households = []
    for region in graph.regions:
        households.extend(region.households)
    agent = Agent(graph, households[0])
    print("===========================================")
    for target_time, events in _events.items():
        print(f"{target_time}: {[str(event) for event in events]}")
    emit(1, AGENT_TRAVERSE, agent)
    emit(1, AGENT_GO_HOME, agent)
    print("===========================================")
    for target_time, events in _events.items():
        print(f"{target_time}: {[str(event) for event in events]}")
    print("===========================================")
    emit(2, AGENT_TRAVERSE, agent)
    for target_time, events in _events.items():
        print(f"{target_time}: {[str(event) for event in events]}")
    print('time: 0', get(0))
    print('time: 1', get(1))
    print('time: 2', get(2))


