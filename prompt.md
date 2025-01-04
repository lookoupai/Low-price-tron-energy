### Project Name
Low-Cost TRON Energy Address Finder Script

### Project Description
Develop an automated script to locate and verify low-cost TRON energy addresses. The script will utilize the TronGrid API or Tronscan API to implement the following functional workflow, replacing the manual steps performed in a browser. The final output should adhere to the specified format, allowing users to quickly identify and utilize low-cost energy addresses.

### Technical Requirements
- **Programming Language**: Python is recommended, but other languages are acceptable based on the developer's proficiency.
- **APIs**: Utilize the [TronGrid API](https://www.trongrid.io/) or [Tronscan API](https://tronscan.org/#/) for data retrieval.
- **Dependencies**: Depending on the chosen programming language, libraries such as `requests` for HTTP requests and JSON parsing libraries may be required.

### Functional Workflow

1. **Fetch the Latest Block**
    - Call the API to retrieve the latest block number.
    - Example API endpoints (may vary based on the chosen API):
      - TronGrid: `https://api.trongrid.io/wallet/getnowblock`
      - Tronscan: `https://apilist.tronscan.org/api/block?sort=-number&limit=1`

2. **Retrieve Block Details**
    - Use the latest block number to get detailed information about the block.
    - Example API endpoints:
      - TronGrid: `https://api.trongrid.io/wallet/getblockbynum?num={block_number}`
      - Tronscan: `https://apilist.tronscan.org/api/block/{block_number}`

3. **Filter "Proxy Resource" Transactions**
    - From the block's transaction list, filter out all transactions of the type "Proxy Resource."

4. **Retrieve "Receiver" Wallet Address Details**
    - For each "Proxy Resource" transaction, extract the "receiver" wallet address.
    - Call the API to get the resource details of the address.
    - Example API endpoints:
      - TronGrid: `https://api.trongrid.io/v1/accounts/{address}`
      - Tronscan: `https://apilist.tronscan.org/api/account?address={address}`

5. **Check the Last 20 Transactions of the Wallet Address**
    - Retrieve the last 20 transaction records of the "receiver" address.
    - Filter out transactions of type "TRX Transfer," token "TRX," and amounts between 0.1-1 TRX.

6. **Validate Transaction Sequence**
    - For each filtered "TRX Transfer" transaction, check if the next transaction record is of type "Proxy Resource."
    - If it matches, mark the "receiver" address as a potential low-cost TRON energy address.

7. **Further Verify Low-Cost TRON Energy Address**
    - For each confirmed low-cost TRON energy address, retrieve its last 10 transactions.
    - Check for "TRX Transfer" transactions, token "TRX," amounts between 0.1-1 TRX, and at least 5 transactions with the same TRX amount.
    - If these conditions are met, confirm the address as a low-cost TRON energy address.

8. **Count the Number of "Proxy Resource" Addresses**
    - Examine the "sender" addresses from the last 10 transactions of the confirmed low-cost TRON energy address.
    - For each "sender" address, repeat steps 4-6 to count the number of "Proxy Resource" addresses obtained.

9. **Determine Address Status**
    - Based on the count of "Proxy Resource" addresses, determine the status of the address:
        - **Normal Use**: More than 7 "Proxy Resource" addresses obtained.
        - **Less Than 100U No Energy**: Fewer than 7 "Proxy Resource" addresses.
        - **Abnormal**: Fewer than 5 "Proxy Resource" addresses.

10. **Record Energy Quantity**
    - During the repetition of steps 4-6, examine "Proxy Resource" transaction records to log each transaction's "energy quantity."
    - For example: “Account TD7Tsy7ZU4BMBaQMYXDAvZi8zso8SRxTky proxies 64,998.86 energy to TRRzeNM2R8S8AxHoUZzzFdV1J9DSwWVhMU,” record the "energy quantity" as “64,998.86 energy.”

11. **Record Purchase Amount**
    - For each low-cost TRON energy address, analyze its latest 10 transactions to record the purchase amount.
    - Confirm the purchase amount: In the latest 10 transactions, if more than 5 transactions have the same TRX transfer amount, record that amount as the "purchase amount."
    - For example, if an address has 6 out of its latest 10 transactions as 1.0 TRX transfers, the "purchase amount" is 1.0 TRX.

12. **Determine Address Status**
    - Based on the counted number of "Proxy Resource" addresses, determine the address status:
        - **Normal Use**: More than 7 "Proxy Resource" addresses obtained.
        - **Less Than 100U No Energy**: Fewer than 7 "Proxy Resource" addresses.
        - **Abnormal**: Fewer than 5 "Proxy Resource" addresses.

13. **Output Results**
    - Only output low-cost TRON energy addresses with statuses "Normal Use" or "Less Than 100U No Energy."
    - Each output address should include the following information along with reference notes:

      ```
      🎉 Real-Time Energy Address Found
      🕵️‍♂️ Each analysis is based on the latest block, ensuring energy addresses remain valid!
  
      🔹 【Energy Address】: TEXwQ99D4nLj14uK9GtXhwGXSYFzmKtUoV
      🔹 【Purchase Records】: https://tronscan.org/#/address/TEXwQ99D4nLj14uK9GtXhwGXSYFzmKtUoV
      🔹 【Purchase Amount】: 1.0 TRX
      🔹 【Energy Quantity】: 65002.35 Energy
  
      【Address Information】In the last 10 transactions, obtained energy is greater than 7, status is normal but does not guarantee energy acquisition.
      ```

      Or

      ```
      🎉 Real-Time Energy Address Found
      🕵️‍♂️ Each analysis is based on the latest block, ensuring energy addresses remain valid!
  
      🔹 【Energy Address】: TYGT2nLqT35Er9bdu9NTCjbWBMTSwKiAYE
      🔹 【Purchase Records】: https://tronscan.org/#/address/TYGT2nLqT35Er9bdu9NTCjbWBMTSwKiAYE
      🔹 【Purchase Amount】: 0.8 TRX
      🔹 【Energy Quantity】: 45000.50 Energy
  
      【Address Information】In the last 10 transactions, obtained energy is fewer than 7, possibly the wallet has less than 100U and does not provide energy.
      ```

    - **Field Descriptions**:
        - **Energy Address**: Confirmed low-cost TRON energy address.
        - **Purchase Records**: Link to the transaction details page of the address.
        - **Purchase Amount**: TRX purchase amount, based on more than 5 identical TRX transfers in the latest 10 transactions, ranging between 0.1-1 TRX.
        - **Energy Quantity**: The amount of TRON energy the address holds.
        - **Reference Information**: Status explanation based on statistical results.

    - **Output Format**: Choose between text files, JSON, Markdown, etc., to ensure information is clear and readable.

### Development Steps Recommendations

1. **Environment Setup**
    - Choose the programming language and set up the development environment.
    - Install necessary dependencies.

2. **API Integration**
    - Implement basic API call functionalities based on the chosen API (TronGrid or Tronscan).
    - Test API connections and data retrieval to ensure they function correctly.

3. **Module Development**
    - Implement each module step-by-step according to the functional workflow, such as fetching block information, filtering transactions, and validating transaction sequences.
    - Perform unit testing for each module upon completion to ensure functionality.

4. **Logic Implementation**
    - Implement complex logic determinations, such as validating transaction sequences and counting "Proxy Resource" addresses.
    - In step 10, implement the recording of energy quantities.
    - In step 11, implement the recording of purchase amounts.
    - Ensure the accuracy and efficiency of the logic.

5. **Result Output**
    - Design and implement methods for storing and outputting results to ensure data is easily readable and analyzable.
    - Generate results according to the specified output format.

6. **Optimization and Error Handling**
    - Add error handling mechanisms to manage API call failures, data anomalies, etc.
    - Optimize script performance to ensure efficiency when handling large data volumes.

7. **Testing and Validation**
    - Conduct testing across multiple blocks and addresses to verify the script's accuracy and stability.
    - Make necessary adjustments and optimizations based on test results.

### Notes
- **API Rate Limits**: Understand the call frequency limits of the chosen API to avoid exceeding limits, which may cause call failures.
- **Data Consistency**: Ensure that the data retrieved is current and accurate, handling potential delays or data inconsistencies.
- **Security**: Properly handle API keys (if any) to prevent leakage of sensitive information.
- **Scalability**: Design the script structure with future functionality expansions in mind.

### Reference Resources
- [TronGrid API Documentation](https://www.trongrid.io/docs)
- [Tronscan API Documentation](https://tronscan.org/#/api-docs)
- [TRON Developer Center](https://developers.tron.network/)
