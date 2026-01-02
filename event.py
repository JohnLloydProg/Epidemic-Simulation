import os

AGENT_GO_WORK = 0
AGENT_TRAVERSE = 1
AGENT_GO_HOME = 2
AGENT_INFECTED = 3
AGENT_REMOVED = 4
EDGE_INFECTION = 5
_events:dict[int, list['Event']] = {}
_time_step = 0

class Event:
    def __init__(self, type:int):
        self.type = type
    
    def __str__(self) -> str:
        return f"Event(type={self.type})"


class AgentEvent(Event):
    _agents:list

    def __init__(self, type:int, agent):
        super().__init__(type)
        self._agent = agent
        self._agents = [agent]
    
    def extends(self, event:'AgentEvent'):
        if (self.type != event.type):
            raise ValueError(f"Event type {self.type} can't extend with event type {self.type}. Agent Event extension is only possible with the same types.")
        
        self._agents.append(event._agent)
    
    def get_agents(self) -> list:
        return self._agents
    
    def __str__(self) -> str:
        return f"AgentEvent(type={self.type}, agents={self._agents})"


def init():
    global _time_step
    _time_step = int(os.environ.get('TIME_STEP', '2'))

def get(time:int) -> list[Event]:
    counter = []
    for target in _events.keys():
        if (target < time):
            counter.append(target)

    if (len(counter) > 0):
        print('current time: ', time)
    for target in counter:
        print("Past events didn't called at target time:", target)
        for event in _events.get(target):
            print(str(event), end=' ')
        print('')

    return _events.pop(time, [])

def emit(target_time:int, event:Event):
    target_time += _time_step - (target_time % _time_step)
    if (target_time in _events):
        events_in_time = _events.get(target_time)
        for target_event in events_in_time:
            if (target_event.type == event.type):
                if (isinstance(target_event, AgentEvent) and isinstance(event, AgentEvent)):
                    target_event.extends(event)
                return
        events_in_time.append(event)
    else:
        _events[target_time] = [event]

if __name__ == '__main__':
    from graphing.mapping import load_graph
    from graphing.graph import Graph
    from agents.agent import Agent
    from dotenv import load_dotenv

    load_dotenv()
    init()
    graph:Graph = load_graph()
    households = []
    for region in graph.regions.values():
        households.extend(region.households)
    agent = Agent(graph, households[0])
    print("===========================================")
    for target_time, events in _events.items():
        print(f"{target_time}: {[str(event) for event in events]}")
    
    emit(1, agent._traverse_event)
    emit(1, agent._go_home_event)
    print("===========================================")
    for target_time, events in _events.items():
        print(f"{target_time}: {[str(event) for event in events]}")
    print("===========================================")
    emit(4, agent._traverse_event)
    for target_time, events in _events.items():
        print(f"{target_time}: {[str(event) for event in events]}")
    print('time: 3', get(3))
    print('time: 4', get(6))
    print('time: 7', get(7))


