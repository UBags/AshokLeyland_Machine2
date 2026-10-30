import requests

# Get data by model name (CSV)
response = requests.post('http://localhost:8000/api/by_model_name/csv/', json={'modelName': 'Model 1'})
print(response.text)

# Get data by model name and date limits (CSV)
response = requests.post('http://localhost:8000/api/by_model_name_and_date/csv/',
                        json={'modelName': 'Model 1', 'startDate': '2025-06-01', 'endDate': '2025-06-29'})
print(response.text)

# Get unique model names (JSON)
response = requests.post('http://localhost:8000/api/get_unique_model_names/json/', json={})
if response.status_code == 200:
    json_data = response.json()['model_names']
    print(json_data)
else:
    print(f"Error: {response.text}")

# Get data by model name (JSON)
response = requests.post('http://localhost:8000/api/by_model_name/json/', json={'modelName': 'Model 1'})
if response.status_code == 200:
    json_data = response.json()['data']  # Access the 'data' key
    print(json_data)  # Prints the list of records
else:
    print(f"Error: {response.text}")