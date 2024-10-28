#!/usr/bin/python3

import requests
import json
import time
import os

# Set the API URL and an array of API keys
api_url = "https://[...]/rest/bug"
api_keys = [""]

current_key_index = 0  # Start with the first key
output_file = 'bug_reports.json'

# Parameters for the API query
params = {
    'limit': 500,  # Limit of results per page
    'offset': 0    # To handle pagination
}

# Load previously saved progress if it exists
def load_existing_data():
    if os.path.exists(output_file):
        with open(output_file, 'r') as f:
            existing_data = json.load(f)
            print(f"Loaded {len(existing_data)} existing bug reports.")
            return existing_data
    return []

# Save the current progress to a file
def save_data(bugs):
    with open(output_file, 'w') as f:
        json.dump(bugs, f, indent=4)
    print(f"Saved {len(bugs)} bug reports to '{output_file}'.")


# Function to fetch comments for a specific bug with retry handling and key rotation
def fetch_comments(bug_id):
    global current_key_index
    comments_url = f"https://bugzilla.suse.com/rest/bug/{bug_id}/comment"
    
    while True:
        try:
            # Attempt to fetch comments with the current API key
            response = requests.get(comments_url, params={'api_key': api_keys[current_key_index]}, timeout=10)
            response.raise_for_status()
            
            # Parse JSON response safely
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
            time.sleep(5)  # Wait briefly before retrying with a new key

        except requests.exceptions.HTTPError as e:
            if response.status_code == 429:
                print(f"Rate limit exceeded with API key {api_keys[current_key_index]}. Switching API key.")
                current_key_index = (current_key_index + 1) % len(api_keys)
                time.sleep(60)
            else:
                print(f"Error fetching comments for Bug #{bug_id}: {e}")
                return None  # Return None to skip this bug if there are other errors

        except requests.exceptions.RequestException as e:
            print(f"Network error for Bug #{bug_id} with API key {api_keys[current_key_index]}: {e}")
            current_key_index = (current_key_index + 1) % len(api_keys)
            time.sleep(5)  # Short delay before switching keys


# Function to fetch bugs with error handling, retry logic, and key rotation
def fetch_bugs(existing_bugs):
    global current_key_index
    max_retries = 5
    params['offset'] = len(existing_bugs)

    while True:
        try:
            response = requests.get(api_url, params={**params, 'api_key': api_keys[current_key_index]})
            response.raise_for_status()

            try:
                bugs = response.json().get('bugs', [])
            except json.JSONDecodeError:
                print("Error decoding JSON response for bugs")
                break
            
            if not bugs:
                print("No more bugs found.")
                break

            # Process each bug
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

            # Save progress after each batch
            save_data(existing_bugs)

            # Increment the offset for pagination
            params['offset'] += len(bugs)
            
            # Delay to avoid hammering the server
            time.sleep(2)

        except requests.exceptions.HTTPError as e:
            if response.status_code == 429:
                print(f"Rate limit exceeded with API key {api_keys[current_key_index]}. Switching API key.")
                current_key_index = (current_key_index + 1) % len(api_keys)  # Move to the next API key
                time.sleep(60)
            else:
                print(f"Error fetching bugs: {e}")
                break
    
    return existing_bugs

# Load existing bug reports if the file exists
existing_bugs = load_existing_data()

# Fetch new bugs starting from where we left off
bugs = fetch_bugs(existing_bugs)

print(f"Downloaded {len(bugs)} bug reports.")

