import os
import sys
import argparse
from typing import Optional
import signal
from dotenv import load_dotenv
from spinner import Spinner
import actions
import response_parser
import check_point
import gpt
import re
import logging
import yaml
import argparse
import time


base_model  = gpt.GPT_3_5_TURBO


class InputTimeoutError(Exception):
    pass

class Assistant:

    GENERAL_DIRECTIONS_PREFIX = """ 
ACTIONS:
    One of your primary responsibilities is to handle a wide range of tasks. This involves:
    1. Understanding the task requirements and context.
    2. Searching for relevant information or resources if necessary.
    3. Executing the task, using your problem-solving abilities to generate the desired outcome.
    4. Documenting the process and outcome in your memory system for future reference and improved decision-making.

    The "RUN_PYTHON" command executes as follows: 
        subprocess.Popen(
            f"python {path} {cmd_args}", // The file {path} contains the Python code you generated.
            shell=True,
            stdout=PIPE,
            stderr=STDOUT,
            universal_newlines=True,
            )
    {"type": "RUN_PYTHON", "path": "<PATH>", "timeout": <TIMEOUT>, "cmd_args": "<ARGUMENTS>", "code": "<CODE>"}
    {"type": "SHUTDOWN", "message": "<TEXT>"} // A concise summary.
    "SEARCH_ONLINE" is used to conduct online searches and retrieve relevant URLs for the query.
    {"type": "SEARCH_ONLINE", "query": "<QUERY>"}
    "EXTRACT_INFO" is used to extract specific information from a URL.
    {"type": "EXTRACT_INFO", "url": "<URL>", "instructions": "<INSTRUCTIONS>"}
    {"type": "APPEND_FILE", "path": "<PATH>", "text": "<TEXT>"}


- Customization of Response Format:
    Bellow is an example response template, While the provided JSON structure outlines the basic requirements for your response, it is not rigid or exhaustive.
    {
        "plan": [ // Must have. It includes measurable step by step tasks.
            "[done] 1. {TASK_DESCRIPTION}",
            "[working] 2. {TASK_DESCRIPTION}, Depends on -> {{task ids}},
            // Final step: verify if the overall goal has been met and generate a user-friendly summary with user guide on what's next.
        ],
        "current_task_id": 2, // Must have.
        "memory": { // Must Have. Everything inside "memory" will be relayed to you in the next conversation.
            "retried_count": "3", // Shutdown after retrying 5 times.
            "thoughts":,
            "reasoning":",
            "next_action": "<ACTION> - <DESCRIPTION>",
            "criticism": ,
            "notebook": { // Must have. 
                "__comments":<COMMENTS>, // put comments here if you have any.
                "reflections": <REFLECTIONS>,
                "progress of subtasks for current task<<$current_task_id>>": [
                    [done]2.1: {SUB-TASK-DESCRIPTION}.success criteria:<INFO>.Verification process:<INFO>,
                    [working]2.2:
                    [todo]2.3:
                    ],
                "lessons_learned": {
                    [{{<LESSON_LEARNED>}}... ]
                },
                "expected_python_code_stdout": <EXPECTED_STDOUT>, // Expected stdout after executing the current Python code when type is "RUN_PYTHON".
                // You are encouraged to add more fields as you deem necessary for effective task execution or for future reference.

                 ...    
            }
        }
        "action": { // Must have.
            "type": "RUN_PYTHON", // One of the above actions.
            // args for the action.
            "path": "{}", // file name for the Python code.
            "timeout": 30, 
            "cmd_args": {ARGUMENTs}, 
            "code":<CODE>, pattern = r"^import"  
        },
    }
"""


    def __init__(self):
        self.memories = ""
        self.tasks_desc = ""

    def input_with_timeout(self, prompt: str, timeout: int) -> Optional[str]:
        signal.signal(signal.SIGALRM, self.signal_handler)
        signal.alarm(timeout)

        try:
            user_input = input(prompt)
            return user_input
        finally:
            signal.alarm(0)

    @staticmethod
    def signal_handler(signum, frame):
        raise InputTimeoutError("Timeout expired")

    @staticmethod
    def extract_exit_code(output):
        match = re.search(r"exit code ([0-9]+)", output)
        return int(match.group(1)) if match is not None else None

    def make_hints(self, action, metadata, action_output):
        hints = "" 
        
        if  isinstance(action, actions.RunPythonAction) and self.extract_exit_code(action_output) != 0:
            if len(self.tasks_desc) > 0:
                hints += "\n\n## Your previous action hit an error, for your reference:\n"
                hints += self.tasks_desc 
        if metadata:
            hints += self.get_plan_hints(metadata)
            hints += self.get_action_hints(metadata, action, action_output)
            if metadata.memory:
                self.memories = self.extrace_memories(metadata)

        hints += f"\n## Memories you have:\nmemory\n{self.memories}" if self.memories else ""

        self.tasks_desc = hints

    @staticmethod
    def extrace_memories(metadata):
        return "{\n" + "\n".join([f"  \"{k}\": {v}," for k, v in metadata.memory.items()]) + "\n}\n" if metadata.memory else ""

    @staticmethod
    def get_plan_hints(metadata):
        return "\n\n## The plan you are using:\n" + "\n".join([f"  - {task}" for task in metadata.plan]) + "\n" if metadata.plan else ""

    @staticmethod
    def get_action_hints(metadata, action, action_output):
        return "\n".join([
                "\n## Your current action returned:\n",
                f"- Task ID: {metadata.current_task_id}\n",
                f"- Task: {action.short_string()}\n",
                f"- Execute Results:\n{action_output}\n"
            ])
    
    def initialize(self, args):
        general_directions = self.GENERAL_DIRECTIONS_PREFIX + "\n\n"
        load_dotenv()
        os.makedirs("workspace", exist_ok=True)
        os.chdir("workspace")
        new_plan: Optional[str] = None
        timeout = args.timeout

        goal = ""
        latest_checkpoint = checkpoint_db.load_checkpoint()
        # If a checkpoint exists, load the metadata from it
        if latest_checkpoint:
            logging.info("\nload checkpoint success\n")

            self.tasks_desc = latest_checkpoint['task_description']
            goal = latest_checkpoint['goal']
        else:
            goal = gpt.revise_goal(input("What would you like me to do:\n"), base_model)

        logging.info("As of my understanding, you want me to do:\n%s\n", goal)

        return goal, new_plan, timeout, general_directions

    def process_action(self, action, metadata, args, timeout, assistant_response):
        if isinstance(action, actions.ShutdownAction):
            logging.info("Shutting down...")
            return False
        if not args.continuous:
            run_action = self.input_with_timeout("Run the action? [Y/n]", timeout)
            if run_action is not None and (run_action.lower() != "y" and run_action != ""):
                return False   
        if action is not None:
            action_output = action.run()
        else:
            self.tasks_desc = f"failed to parse assistant response, is it valid json: {assistant_response}"
            return True
        
        self.make_hints(action, metadata, action_output)
            
        return True

    def run(self, args):
        goal, new_plan, timeout, general_directions = self.initialize(args)
        refresh = False

        while True:
            action = None
            metadata = None
            try:
                logging.info("========================")
                with Spinner("Thinking..."):
                    try:
                        if refresh:
                            assistant_resp = gpt.chat(goal,
                                                      "Reflect and execute plan to match the Goal!\n" + general_directions,
                                                      self.tasks_desc, model=base_model)
                            refresh = False
                        else:
                            assistant_resp = gpt.chat(goal, general_directions, self.tasks_desc, model=base_model)
                    except Exception as err:
                        logging.info("%s", err)
                        continue

                if args.verbose:
                    logging.info("ASSISTANT RESPONSE: %s", assistant_resp)
                action, metadata = response_parser.parse(assistant_resp)
                
                if not self.process_action(action, metadata, args, timeout, assistant_resp):
                    break
                # saving the checkpoint after every iteration
                checkpoint_db.save_checkpoint(self.tasks_desc, goal)

            except Exception as err:
                logging.exception("Error in main: %s", err)
                self.make_hints(action, metadata, str(err))
                checkpoint_db.save_checkpoint(self.tasks_desc, goal)
                time.sleep(1)

                continue
            
            print(f"\n\ncurrent plan: {metadata.plan}\n")
            new_plan = self.get_new_plan(timeout)

            if new_plan:     #refresh the goal, since we changed the plan
                goal = gpt.revise_goal(
                    "Given the following context:\n\n" +
                    f"Original goal: {goal}\n" +
                    "Original plan: \n" +
                    f"{metadata.plan}\n" +
                    "Proposed change to the plan: \n" +
                    f"{new_plan}\n\n" +
                    "Please provide a revised goal that corresponds with this proposed change in the plan. Only state the revised goal.",
                    base_model
                )

                logging.info("\n\nThe new goal is: %s\n\n", goal)
                new_plan = None
                refresh = True


    def get_new_plan(self, timeout: int) -> Optional[str]:
        try:
            change_plan = self.input_with_timeout("Change the proposed plan? [N/y]", timeout)
        except InputTimeoutError:
            logging.info("Input timed out. Continuing with the current plan...")
            change_plan = None

        if change_plan is not None and change_plan.lower() == "y":
            return input("\nWould you like to change your plan? \nChanges you want to make:")
        else:
            return None



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=str, default='config.yaml', help='Path to the configuration file')
    parser.add_argument('--timeout', type=int, default=1, help='Timeout for user input')  
    parser.add_argument('--continuous', action='store_true', help='Continuous mode')  # Add this line

    args = parser.parse_args()

    # Load configuration from YAML file
    with open(args.config, 'r') as f:
        config = yaml.safe_load(f)

   # Database configuration
    db_config = config.get('database', {})
    db_name = db_config.get('name', 'jarvis')
    db_user = db_config.get('user', '2bw9XzdKWiSnJgo.root')
    db_password = db_config.get('password', 'password')
    db_host = db_config.get('host', 'localhost')
    db_port = db_config.get('port', 4000)
    ssl = db_config.get('ssl', None)

    # Create an instance of CheckpointDatabase
    checkpoint_db = check_point.CheckpointDatabase(db_name, db_user, db_password, db_host, db_port, ssl)

    # GPT model configuration
    gpt_model = config.get('gpt', {}).get('model', base_model)

    # Logging configuration
    logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)
   
    assistant_config = config.get('assistant', {})
    args.timeout = args.timeout or assistant_config.get('timeout', 30)
    args.verbose = assistant_config.get('verbose', False)
    args.continuous = args.continuous or assistant_config.get('continuous', False)

    checkpoint_db.create_table()

    # Instantiate and start assistant
    assistant = Assistant()
    assistant.run(args)

