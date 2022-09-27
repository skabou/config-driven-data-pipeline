# Configuration Driven Data Pipeline - Basic Concept and Implementation

[![pypi](https://img.shields.io/pypi/v/cddp.svg)](https://pypi.org/project/cddp)

## Why this solution

This repository is a solution of data pipeline that is driven by a configuration file. The configuration file is a JSON file that contains the information about the data sources, the data transformations and the data curation. The configuration file is the only file that needs to be modified to change the data pipeline. **In this way, even business users or operation team can modify the data pipeline without the need of a developer.**

This repository shows a simplified version of this solution based on [Azure Databricks](https://learn.microsoft.com/en-us/azure/databricks/scenarios/what-is-azure-databricks), [Apache Spark](https://spark.apache.org/docs/latest/index.html) and [Delta Lake](https://www.delta.io). The configuration file is converted into Azure Databricks Job as the runtime of the data pipeline. It targets to provide a lo/no code data app solution for business or operation team.

## Background

![medallion architecture](https://github.com/maye-msft/simple-configurable-data-pipeline/blob/main/images/arc1.png)

This is the medallion architecture introduced by Databricks. And it shows a data pipeline which includes three stages: Bronze, Silver, and Gold. In most data platform projects, the stages can be named as Staging, Standard and Serving.

- Bronze/Staging: The data from external systems is ingested and stored in the staging stage. The data structures in this stage correspond to the source system table structures "as-is," along with any additional metadata columns that capture the load date/time, process ID, etc.
- Silver/Standardization: The data from the staging stage is cleansed and transformed and then stored in the standard stage. It provides enriched datasets for further business analysis. The master data could be versioned with slowly changed dimension (SCD) pattern and the transaction data is deduplicated and contextualized with master data.
- Gold/Serving: The data from the standard stage is aggregated and then stored in the serving stage. The data is organized in consumption-ready "project-specific" databases, such as Azure SQL.

![Azure](https://github.com/maye-msft/simple-configurable-data-pipeline/blob/main/images/arc2.png)

The above shows a typical way to implement a data pipeline and data platform based on Azure Databricks.

- Azure Data Factory can be used to load external data and store to Azure Data Lake Storage.
- Azure Data Lake Storage (ADLS) can be applied as the storage layer of the staging, standard, and serving stage.
- Azure Databricks is the calculation engine for data transformation, and most of the transformation logic can be implemented with pySpark or SparkSQL.
- Azure Synapse Analytics or Azure Data Explorer is the solution of serving stage.
The medallion architecture and Azure big data services consist of the infrastructure of an enterprise data platform. Then data engineers can build transformation and aggregation logic with programming languages such as Scala, Python, SQL etc. Meanwhile, DevOps is mandatory in the modern data warehouse.

## Architecture

![Architecture](https://github.com/maye-msft/simple-configurable-data-pipeline/blob/main/images/arc3.png)

Inspired by Data Mesh, we try to create a solution to accelerate the data pipeline implementation and reduce the respond time to changing business needs, where we’d like to help business team can have the ownership of data application instead of data engineers, who could focus on the infrastructure and frameworks to support business logic more efficiently.

The configurable data pipeline includes two parts

- Framework: The framework is to load the configuration files and convert them into Spark Jobs which could be run with Databricks. It encapsulates the complex Spark cluster and job runtime and provides a simplified interface to users, who can focus on business logic. The framework is based on pySpark and Delta Lake and managed by developers.
- Configuration: It is the metadata of the pipeline, which defines the pipeline stages, data source information, transformation and aggregation logic which can be implemented in SparkSQL. And the configuration can be managed by a set of APIs. The technical operation team can manage the pipeline configuration via a Web UI based on the API layer.
Proof of Concept:
We need to build a data pipeline to calculate the total revenue of fruits.

![PoC](https://github.com/maye-msft/simple-configurable-data-pipeline/blob/main/images/poc.png)

There are 2 data sources:

- fruit price – the prices could be changed frequently and saved as CSV files which upload into the landing zone.
- fruit sales – it is streaming data when a transition occurs, an event will be omitted. And the data is saved as CSV file into a folder of landing zone as well.
In the standardized zone, the price and sales view can be joined. Then in the serving zone, the fruit sales data can be aggregated.
The JSON file below describes the pipeline.

### Configuration file

```json
{
  "name": "fruit-data-app",
  "staging": {
    "sales": {
      "format": "csv",
      "target": "raw_sales",
      "location": "landing/sales/",
      "type": "streaming",
      "output": ["table"],
      "schema": {...}
    },
    "price": {
      "format": "csv",
      "target": "raw_price",
      "location": "landing/price/",
      "type": "batch",
      "output": ["table"],
      "schema": {...}
    }
  },
  "standard": {
    "fruit_sales": {
      "sql": "select price.fruit, price.id, sales.amount, price.price, sales.ts from raw_sales sales left outer join raw_price price on sales.id = price.id and sales.ts >= price.start_ts and sales.ts < price.end_ts",
      "target": "fruit_sales",
      "type": "batch",
      "output": ["table"]
    }
  },
  "serving": {
    "fruit_sales_total": {
      "sql": "select id, fruit, sum(amount*price) as total from fruit_sales group by id, fruit order by total desc",
      "target": "fruit_sales_total",
      "type": "batch",
      "output": ["file"]
    }
  }
}
```

In the pipeline, it includes the 3 blocks:

- **staging**
- **standardization**
- **serving**

The staging block defines the data sources. The standardization block defines the transformation logic. The serving block defines the aggregation logic.
Spark SQL are used in the standardization block and the serving block, one is merge price and sales data and the other is for aggregation of the sales data.

Here is a simplified version of the framework. It is built with a Databricks notebook in python.

Here is the full [JSON file](pipeline_fruit.json) of this example pipeline. And [another more complex example pipeline](pipeline_nyc_taxi.json) in this repo which is a data pipeline to analyze NYC taxi pickup data.

### Framework

There are 3 functions defined in the notebook.

- start_staging_job: 
  - It is to load the data from the data sources and save them to the staging zone.
  - It supports to load data in streaming or batch mode.

```python
def start_staging_job(spark, config, name, timeout=None):
    schema = StructType.fromJson(config["staging"][name]["schema"])
    location = config["staging"][name]["location"]
    target = config["staging"][name]["target"]
    type = config["staging"][name]["type"]
    output = config["staging"][name]["output"]
    format = config["staging"][name]["format"]
    if type == "streaming":
        df = spark \
            .readStream \
            .format(format) \
            .option("multiline", "true") \
            .option("header", "true") \
            .schema(schema) \
            .load(storage_dir_path+"/"+location)    

        if "table" in output:
            query = df.writeStream\
                .format("delta") \
                .outputMode("append")\
                .option("checkpointLocation", staging_path+"/"+target+"_chkpt")\
                .toTable(target)
            if timeout is not None:
                query.awaitTermination(timeout)
        if "file" in output:
            query = df.writeStream \
                .format("delta") \
                .outputMode("append") \
                .option("checkpointLocation", staging_path+"/"+target+"_chkpt") \
                .start(staging_path+"/"+target)
            if timeout is not None:
                query.awaitTermination(timeout)
        if "view" in output:
            df.createOrReplaceTempView(target)

    elif type == "batch":
        df = spark \
            .read \
            .format(format) \
            .option("multiline", "true") \
            .option("header", "true") \
            .schema(schema) \
            .load(storage_dir_path+"/"+location)  

        if "table" in output:
            df.write.format("delta").mode("append").option("overwriteSchema", "true").saveAsTable(target)
        if "file" in output:
            df.write.format("delta").mode("append").option("overwriteSchema", "true").save(staging_path+"/"+target)
        if "view" in output:
            df.createOrReplaceTempView(target)

    else :
        raise Exception("Invalid type")
```

- start_stardard_job:
  - It is to load the data from the staging zone and transform them to the standard zone.
  - This function supports to run in batch mode or streaming mode, the streaming mode is available when there are view from streaming data source.

```python
def start_standard_job(spark, config, name, timeout=None):
    sql = config["standard"][name]["sql"]
    output = config["standard"][name]["output"]
    type = config["standard"][name]["type"]
    if(isinstance(sql, list)):
        sql = " \n".join(sql)
    target = config["standard"][name]["target"]
    load_staging_views()
    df = spark.sql(sql)
    if type == "streaming":
        if "table" in output:
            query = df.writeStream\
                .format("delta") \
                .outputMode("append")\
                .option("checkpointLocation", staging_path+"/"+target+"_chkpt")\
                .toTable(target)
            if timeout is not None:
                query.awaitTermination(timeout)
        if "file" in output:
            query = df.writeStream \
                .format("delta") \
                .outputMode("append") \
                .option("checkpointLocation", staging_path+"/"+target+"_chkpt") \
                .start(staging_path+"/"+target)
            if timeout is not None:
                query.awaitTermination(timeout)
        if "view" in output:
            df.createOrReplaceTempView(target)
    elif type == "batch":
        if "table" in output:
            df.write.format("delta").mode("append").option("overwriteSchema", "true").saveAsTable(target)
        if "file" in output:
            df.write.format("delta").mode("append").option("overwriteSchema", "true").save(standard_path+"/"+target)
        if "view" in output:
            df.createOrReplaceTempView(target)
    else :
        raise Exception("Invalid type")
```

- start_serving_job:
  - It is to load the data from the standard zone and aggregate them to the serving zone.
  - This function supports to run in batch mode or streaming mode, the streaming mode is available when there are view from streaming data source.

```python
def start_serving_job(spark, config, name, timeout=None):
    sql = config["serving"][name]["sql"]
    output = config["serving"][name]["output"]
    if(isinstance(sql, list)):
        sql = " \n".join(sql)
    target = config["serving"][name]["target"]
    type = "batch"
    if "type" in config["serving"][name]:
        type = config["serving"][name]["type"]
    load_staging_views()
    load_standard_views()
    df = spark.sql(sql)
    if type == "streaming":
        if "table" in output:
            query = df.writeStream\
                .format("delta") \
                .outputMode("complete")\
                .option("checkpointLocation", serving_path+"/"+target+"_chkpt")\
                .toTable(target)
            if timeout is not None:
                query.awaitTermination(timeout)
        if "file" in output:
            query = df.writeStream \
                .format("delta") \
                .outputMode("complete") \
                .option("checkpointLocation", serving_path+"/"+target+"_chkpt") \
                .start(serving_path+"/"+target)
            if timeout is not None:
                query.awaitTermination(timeout)
        else:
            raise Exception("Invalid output type")
    elif type == "batch":
        if "table" in output:
            df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)
        if "file" in output:
            df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(serving_path+"/"+target)
        else:
            raise Exception("Invalid output type")
    else :
        raise Exception("Invalid type")
```

The pipeline configuration file is loaded by the notebook, where the file path is an input of the notebook.

```python
def load_config(path) :
    with open(path, 'r') as f:
        config = json.load(f)
    return config
```

Finally, the notebook starts all the tasks in each stage.

```python
if 'staging' in config:
    for name in config["staging"]:
        start_staging_job(spark, config, name)
if 'standard' in config:
    for name in config["standard"]:
        start_standard_job(spark, config, name)
if 'serving' in config:
    for name in config["serving"]:
        start_serving_job(spark, config, name)
```

We create one Databricks job to run this notebook. Here is the screenshot to create the Databricks job and task.

![job config](https://github.com/maye-msft/simple-configurable-data-pipeline/blob/main/images/job1.png)

After running the job, the data output to the serving path will be as below.

  id|      fruit|total
----|-----------|------
   4|Green Apple| 90.0
   7|Green Grape| 72.0
   5| Fiji Apple|112.0
   1|  Red Grape| 48.0
   3|     Orange| 56.0
   6|     Banana| 34.0
   2|      Peach| 78.0

### Job Parallelism

Databricks supports to execute tasks in parallel, the tasks in a job can be organized as a graph based on the dependency of tasks.

![job config](https://github.com/maye-msft/simple-configurable-data-pipeline/blob/main/images/job2.png)

And the four tasks in the pipeline can be organized as below

![job config](https://github.com/maye-msft/simple-configurable-data-pipeline/blob/main/images/job3.png)
  
## Conclusion

It introduces a method to build a data pipeline with configuration file and demonstrates it through a Databricks notebook and JSON based configuration file. And we can create an API layer on the top of the Databricks REST API and then the data pipeline development work could be shifted from data engineering team.

## Next Step

While the following topics still need to be considered before go-to-production.

- Data persistence: In staging and standardization zone, the data can be converted into parquet format and stored into Azure Data Lake Storage to have better performance. While in standardization zone, temporary view could be applied for intermediate result when no persistence is required.
- Slow Changed Dimension (SCD): Slowly Changing Dimensions are dimensions which change over time, and it is a pattern to keep the accuracy of the report in the data pipeline which can track the changes of the attributes. Here is an SCD implementation which could be used in the framework. [https://pypi.org/project/dbxscd/](https://pypi.org/project/dbxscd/)
- CI/CD: We can leverage DevOps pipeline to automatically complete the notebook testing and deployment, here is one template of Azure DevOps pipeline for Databricks notebook. [link](https://github.com/Azure-Samples/modern-data-warehouse-dataops/tree/main/single_tech_samples/databricks/sample4_ci_cd)
- Error Handling: Databricks provide mature solution to handle bad record while we need to design and implementation retry solution. [Handling bad records and files - Azure Databricks | Microsoft Docs](https://docs.microsoft.com/en-us/azure/databricks/spark/latest/spark-sql/handling-bad-records)
- Monitoring: Azure Monitor and Azure Log Analytics are the tools which can integrated with Azure Databricks, here is a tutorial to build the Databricks monitoring. [Monitor Azure Databricks - Azure Architecture Center | Microsoft Docs](https://docs.microsoft.com/en-us/azure/architecture/databricks-monitoring/)

## Reference

- [Medallion Architecture – Databricks](https://www.databricks.com/glossary/medallion-architecture)

- [How to Use Databricks to Scale Modern Industrial IoT Analytics - Part 1 - The Databricks Blog](https://www.databricks.com/blog/2020/08/03/modern-industrial-iot-analytics-on-azure-part-1.html)

- [How to Implement CI/CD on Databricks Using Databricks Notebooks and Azure DevOps - The Databricks Blog](https://www.databricks.com/blog/2021/09/20/part-1-implementing-ci-cd-on-databricks-using-databricks-notebooks-and-azure-devops.html)

- [Break Through the Centralized Platform Bottlenecks with Data Mesh | Thoughtworks](https://www.thoughtworks.com/en-sg/insights/articles/break-though-the-centralized-platform-bottlenecks-with-data-mesh)

## Appendix

### Run the demonstration locally

- Clone the repository

```bash
git clone https://github.com/maye-msft/simple-configurable-data-pipeline
cd simple-configurable-data-pipeline
```

- Install the dependencies

```bash
python -m venv .venv
cd .venv
cd Scripts
activate
cd ../..
pip install -r requirements.txt
```

- Run fruit app

```bash
python src/main.py --config-path ./example/pipeline_fruit.json --landing-path ./data/landing/fruit_data_app/ --working-dir ./tmp --show-result True 
```

- Run fruit app in parallel

```bash
python src/main.py --config-path ./example/pipeline_fruit_parallel.json --landing-path ./data/landing/fruit_data_app/ --working-dir ./tmp --await-termination 30 --show-result True 
```

- Run NYC taxi app with python

```bash
python src/main.py --config-path ./example/pipeline_nyc_taxi.json --landing-path ./data/landing/nyc_taxi/ --working-dir ./tmp --show-result True 
```

- Start the WebUI

```bash
flask --app src/api run
```

Then visit http://127.0.0.1:5000/static/index.html to open the WebUI as shown below.

![job config](https://github.com/maye-msft/simple-configurable-data-pipeline/blob/main/images/webUI.png)
