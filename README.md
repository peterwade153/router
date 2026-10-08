# router
Truck route planner, with truck fuel stops with least fuel price per gallon

### Installation

Prerequsites, Docker installation

1. Create and activate a virtual environment and Clone the project `https://github.com/peterwade153/router.git`

2. Move into the project folder
   ```
    cd router
   ```

3. Create a `.env` file from the `.env.sample` file.

4. Start Docker and run command below
    ```bash
    docker compose up
    ```

    The API will be available at:

    ```text
    http://127.0.0.1:8000/
    ```

 Test in Postman with the Sample Payloads.
   - State is mandatory. For Optimal Routing, include the City and County.

   ```json
      {
         "origin": {
            "city": "Houston",
            "state": "TX"
         },
         "destination": {
            "city": "Chicago",
            "state": "IL"
         }
      }
   ```
   ```json
      {
         "origin": {
            "state": "PA"
         },
         "destination": {
            "city": "Chicago",
            "state": "IL"
         }
      }
   ```
   ```json
      {
         "origin": {
            "city": "Jackson",
            "county": "Teton",
            "state": "WY"
         },
         "destination": {
            "city": "Houston",
            "county": "Harris County",
            "state": "TX"
         }
      }
   ```

5. Seeding Data for US cities and Truck stop

   ```bash
      docker compose exec web python manage.py seed_data 
   ```

6. Run test

   ```bash
      docker compose exec web python manage.py test 
   ```