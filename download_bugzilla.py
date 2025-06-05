#!/usr/bin/python3
import requests
import json
import time
import os
import datetime


# Set the API URL and an array of API keys
api_url = "https://[...]/rest/bug"
api_keys = [""]

current_key_index = 0  # Start with the first key
output_file = 'bug_reports.json'

params = {
    'limit': 500,
    'offset': 0
}


def load_existing_data():
    if os.path.exists(output_file):
        try:
            with open(output_file, 'r') as f:
                existing_data = json.load(f)
                print(f"Loaded {len(existing_data)} existing bug reports.")
                return existing_data
        except json.JSONDecodeError:
            backup_file = output_file + ".corrupt_backup"
            os.rename(output_file, backup_file)
            print(f"Warning: Failed to parse '{output_file}'. Backed up to '{backup_file}'. Starting fresh.")
            return []
    return []



def save_data(bugs):
    try:
        with open(output_file, 'w') as f:
            json.dump(bugs, f, indent=4)
    except (IOError, OSError) as e:
        print(f"Failed to save data: {e}")

def fetch_comments(bug_id):
    global current_key_index
    comments_url = f"https://bugzilla.suse.com/rest/bug/{bug_id}/comment"
    network_error_attempts = 0
    
    while True:
        try:
            response = requests.get(comments_url, params={'api_key': api_keys[current_key_index]}, timeout=10)
            response.raise_for_status()

            comments_data = response.json().get('bugs', {}).get(str(bug_id), {}).get('comments', [])
            comments_list = []
            for comment in comments_data:
                comment_record = {
                    "name": comment.get('creator', 'Unknown'),
                    "date": comment.get('creation_time', 'Unknown'),
                    "text": comment.get('text', '')
                }
                comments_list.append(comment_record)
            return comments_list

        except requests.exceptions.Timeout:
            print(f"Connection timed out for API key {api_keys[current_key_index]}. Switching API key.")
            current_key_index = (current_key_index + 1) % len(api_keys)
            time.sleep(5)

        except requests.exceptions.HTTPError as e:
            status = response.status_code
            if status == 429:
                print(f"Rate limit exceeded with API key {api_keys[current_key_index]}. Switching API key.")
                current_key_index = (current_key_index + 1) % len(api_keys)
                time.sleep(60)
            elif status == 503:
                print(f"Service unavailable for Bug #{bug_id}. Retrying with next API key after backoff.")
                current_key_index = (current_key_index + 1) % len(api_keys)
                time.sleep(30)
                network_error_attempts += 1
            else:
                print(f"Error fetching comments for Bug #{bug_id}: {e}")
                return None


        except requests.exceptions.RequestException as e:
            wait_time = min(60 * (2 ** network_error_attempts), 43200)
            print(f"Network error for Bug #{bug_id} with API key {api_keys[current_key_index]}: {e}")
            print(f"Waiting {wait_time // 60}m {wait_time % 60}s before retrying...")
            current_key_index = (current_key_index + 1) % len(api_keys)
            time.sleep(wait_time)
            network_error_attempts += 1


def fetch_bugs(existing_bugs, params):
    global current_key_index
    params['offset'] = len(existing_bugs)
    bugs_since_last_save = 0

    while True:
        try:
            response = requests.get(api_url, params={**params, 'api_key': api_keys[current_key_index]}, timeout=10)
            response.raise_for_status()

            try:
                data = response.json()
                if 'bugs' not in data:
                    print("Unexpected response structure: 'bugs' key not found.")
                    print("Raw response:", data)
                    time.sleep(60)
                    continue
                bugs = data['bugs']
            except json.JSONDecodeError:
                print("Error decoding JSON response for bugs")
                break

            if not bugs:
                print("No more bugs found.")
                break

            for bug in bugs:
                print(f"Fetching Bug #{bug.get('id')}: {bug.get('summary', 'No title available')}")
                comments = fetch_comments(bug.get('id'))

                if comments is None:
                    save_data(existing_bugs)
                    print("Exiting due to failure to fetch comments.")
                    return existing_bugs

                bug_record = {
                    "bug_number": bug.get('id', ''),
                    "title": bug.get('summary', 'No title available'),
                    "Product": bug.get('product', 'Unknown'),
                    "version": bug.get('version', 'Unknown'),
                    "Component": bug.get('component', 'Unknown'),
                    "Reported": bug.get('creation_time', 'Unknown'),
                    "Status": bug.get('status', 'Unknown'),
                    "Comments": comments
                }

                existing_bugs.append(bug_record)
                bugs_since_last_save += 1

                if bugs_since_last_save >= 500:
                    save_data(existing_bugs)
                    print(f"Saved after {bugs_since_last_save} new bugs.")
                    bugs_since_last_save = 0

            params['offset'] += len(bugs)
            time.sleep(2)

        except requests.exceptions.HTTPError as e:
            if response.status_code == 429:
                print(f"Rate limit exceeded with API key {api_keys[current_key_index]}. Switching API key.")
                current_key_index = (current_key_index + 1) % len(api_keys)
                time.sleep(60)
            else:
                print(f"Error fetching bugs: {e}")
                break

        except requests.exceptions.RequestException as e:
            print(f"Request failed at offset {params['offset']} with API key {api_keys[current_key_index]}: {e}")
            current_key_index = (current_key_index + 1) % len(api_keys)
            time.sleep(30)
            continue

    # Final save
    if bugs_since_last_save > 0:
        save_data(existing_bugs)
        print(f"Final save after last {bugs_since_last_save} bugs.")

    return existing_bugs


if __name__ == "__main__":
    start_time = datetime.datetime.now()
    existing_bugs = load_existing_data()

    try:
        bugs = fetch_bugs(existing_bugs, params)
        duration = (datetime.datetime.now() - start_time).total_seconds()
        print(f"Processed {len(bugs)} bugs in {duration:.1f} seconds.")
    except KeyboardInterrupt:
        print("\nInterrupted by user. Saving data and exiting gracefully...")
        save_data(existing_bugs)
        print("Data saved. Goodbye.")
    except requests.exceptions.RequestException as e:
        print(f"Network error occurred during bug fetching: {e}")
        save_data(existing_bugs)
        print("Exiting due to persistent network issues.")
