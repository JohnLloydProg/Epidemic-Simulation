import json

class Instruction:
    def __init__(self, operation, dest, operand_1, operand_2):
        self.timeline = {"f": 0, "d": 0, "e": 0, "w": 0}
        self.operation = operation
        self.dest = dest
        self.operand_1 = operand_1
        self.operand_2 = operand_2
        self.complete = False
    
    def __str__(self):
        return f"{self.operation} {self.dest}, {self.operand_1}, {self.operand_2}"
    
    def registers_used(self):
        return [self.dest, self.operand_1, self.operand_2]

def print_timeline(instruction:Instruction) -> str:
    line = ""
    for i in range(1, complete_time + 1):
        stage = None
        for s, t in instruction.timeline.items():
            if i >= t and i < t + stages[s]["duration"]:
                stage = s
                break
        if stage:
            line += stage.upper()
        else:
            if instruction.timeline["f"] < i < instruction.timeline["w"]:
                line += "S"
            else:
                line += "."
    return line

stages = {
    "f" : {"instruction": None, "duration": 1},
    "d" : {"instruction": None, "duration": 1},
    "e" : {"instruction": None, "duration": 1},
    "w" : {"instruction": None, "duration": 1},
}

instructions = [
    Instruction("add", "R1", "R2", "R3"),
    Instruction("add", "R4", "R5", "R6"),
    Instruction("sub", "R2", "R4", "R5"),
    Instruction("sub", "R1", "R4", "R6")
]
fetched = []
counters = {stage: 0 for stage in stages.keys()}
registers_in_use = set()
pc = 0
clock = 0
complete_time = 0

while True:
    if all(inst.complete for inst in instructions):
        complete_time = clock
        break
    
    clock += 1
    
    if stages["w"]["instruction"]:
        if clock - counters["w"] >= stages["w"]["duration"]:
            stages["w"]["instruction"].complete = True
            registers_in_use.difference_update(set(stages["w"]["instruction"].registers_used()))
            stages["w"]["instruction"] = None
    
    if stages["e"]["instruction"]:
        if clock - counters["e"] >= stages["e"]["duration"] and not stages["w"]["instruction"]:
            stages["w"]["instruction"] = stages["e"]["instruction"]
            stages["w"]["instruction"].timeline["w"] = clock
            counters["w"] = clock
            stages["e"]["instruction"] = None
    
    if stages["d"]["instruction"]:
        if clock - counters["d"] >= stages["d"]["duration"] and not stages["e"]["instruction"]:
            stages["e"]["instruction"] = stages["d"]["instruction"]
            stages["e"]["instruction"].timeline["e"] = clock
            counters["e"] = clock
            stages["d"]["instruction"] = None
    
    if fetched:
        if not registers_in_use.intersection(set(fetched[0].registers_used())):
            ins = fetched.pop()
            registers_in_use.update(ins.registers_used())
            stages["d"]["instruction"] = ins
            stages["d"]["instruction"].timeline["d"] = clock
            counters["d"] = clock
    
    if stages["f"]["instruction"] is None and pc < len(instructions):
        fetched.append(instructions[pc])
        instructions[pc].timeline["f"] = clock
        pc += 1

    print(f"Time {str(clock)}: {json.dumps(stages, default=lambda o: str(o), indent=2)}")

for inst in instructions:
    print(f"Instruction {str(inst)} timeline: {print_timeline(inst)}")
