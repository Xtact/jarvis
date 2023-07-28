import logging
import os
from typing import Dict, List

import yaml

from smartgpt.translator import Translator
from smartgpt.reviewer import Reviewer


class Compiler:
    def __init__(self, translator_model: str, reviewer_model: str):
        self.translator = Translator(translator_model)
        self.reviewer = Reviewer(reviewer_model, self.translator)

    def load_yaml(self, file_name: str) -> Dict:
        try:
            with open(file_name, 'r') as stream:
                return yaml.safe_load(stream)
        except Exception as e:
            logging.error(f"Error loading file {file_name}: {e}")
            raise

    def write_yaml(self, file_name: str, data: str) -> None:
        try:
            with open(file_name, "w") as stream:
                stream.write(data)
        except Exception as e:
            logging.error(f"Error writing to file {file_name}: {e}")
            raise

    def create_task_info(self, task, num, hints, previous_outcomes) -> Dict:
        return {
            "first_task": num == 1,
            "task_num": num,
            "hints": hints,
            "task": task,
            "start_seq": (num - 1 << 4) + 1,
            "previous_outcomes": previous_outcomes
        }

    def check_diff(self, task_outcome, origin) -> bool:
        return task_outcome['overall_outcome'] != origin['overall_outcome']

    def compile_plan(self) -> List[Dict]:
        plan = self.load_yaml('plan.yaml')

        hints = plan.get("hints_from_user", [])
        task_list = plan.get("task_list", [])
        task_dependency = plan.get("task_dependency", {})
        task_outcomes = {}
        result = []

        for task in task_list:
            num = task['task_num']
            deps = task_dependency.get(str(num), [])
            previous_outcomes = [task_outcomes[i] for i in deps]
            file_name = f"{num}.yaml"

            task_info = self.create_task_info(task['task'], num, hints, previous_outcomes)
            instructions_yaml_str = self.reviewer.translate_to_instructions(task_info)
            self.write_yaml(file_name, instructions_yaml_str)
            task_outcome = yaml.safe_load(instructions_yaml_str)

            result.append(task_outcome)
            task_outcomes[num] = {
                "task_num": num,
                "task": task_outcome['task'],
                "outcome": task_outcome['overall_outcome'],
            }

        return result

    def compile_task_in_plan(self, specified_task_num: int) -> List[Dict]:
        plan = self.load_yaml('plan.yaml')

        hints = plan.get("hints_from_user", [])
        task_list = plan.get("task_list", [])
        task_dependency = plan.get("task_dependency", {})
        task_outcomes = {}
        result = []
        need_to_recompile_subsequent_tasks = False

        for task in task_list:
            num = task['task_num']
            deps = task_dependency.get(str(num), [])
            previous_outcomes = [task_outcomes[i] for i in deps]
            file_name = f"{num}.yaml"

            task_info = self.create_task_info(task['task'], num, hints, previous_outcomes)
            origin = self.load_yaml(file_name) if os.path.exists(file_name) else None

            task_outcome = None
            if num < specified_task_num and os.path.exists(file_name):
                task_outcome = self.load_yaml(file_name)
            elif num > specified_task_num and os.path.exists(file_name) and not need_to_recompile_subsequent_tasks:
                task_outcome = self.load_yaml(file_name)

            if not task_outcome:
                instructions_yaml_str = self.reviewer.translate_to_instructions(task_info)
                self.write_yaml(file_name, instructions_yaml_str)
                task_outcome = yaml.safe_load(instructions_yaml_str)

            if num == specified_task_num:
                need_to_recompile_subsequent_tasks = self.check_diff(task_outcome, origin) if origin else True

            result.append(task_outcome)
            task_outcomes[num] = {
                "task_num": num,
                "task": task_outcome['task'],
                "outcome": task_outcome['overall_outcome'],
            }

        return result

    def compile_task(self, specified_task_num: int, task: str, hints: List, previous_outcomes: List) -> Dict:
        file_name = f"{specified_task_num}.yaml"
        task_info = self.create_task_info(task, specified_task_num, hints, previous_outcomes)

        instructions_yaml_str = self.reviewer.translate_to_instructions(task_info)
        self.write_yaml(file_name, instructions_yaml_str)
        result = yaml.safe_load(instructions_yaml_str)

        return result