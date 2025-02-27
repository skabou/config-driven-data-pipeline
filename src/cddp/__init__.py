import json
import os 
from pyspark import SparkConf
from pyspark.sql import SparkSession
from pyspark.sql.types import *
from sys import argv
import shutil
from delta import *
import pyspark
from delta.tables import *
import argparse
import time
import tempfile
import uuid

storage_format = "delta"

def create_spark_session():
    """Creates a Spark Session"""
    builder = pyspark.sql.SparkSession.builder.appName("MyApp") \
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension") \
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    spark = configure_spark_with_delta_pip(builder).getOrCreate()
    return spark

def init(spark, config, working_dir):
    """Delete the folders for the data storage"""
    current_dir_path = os.path.dirname(os.path.realpath(__file__))
    if working_dir is None:
        working_dir = current_dir_path
    config['working_dir'] = working_dir

    app_name = config['name']
    config['app_data_path'] = f"{config['working_dir']}/{app_name}/"
    config['staging_path'] = f"{config['working_dir']}/{app_name}/staging"
    config['standard_path'] = f"{config['working_dir']}/{app_name}/standard"
    config['serving_path'] = f"{config['working_dir']}/{app_name}/serving"
    

    print(f"""app name: {config["name"]},
    landing path: {config['landing_path']},
    staging path: {config['staging_path']},
    standard path: {config['standard_path']},
    serving path: {config['serving_path']},
    working dir:{config['working_dir']},
    """)



def init_database(spark, config):
    app_name = config['name']
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {app_name}")
    spark.sql(f"USE SCHEMA {app_name}")

def clean_database(spark, config):
    app_name = config['name']
    current_dir_path = os.path.dirname(os.path.realpath(__file__))
    database_path = f"{current_dir_path}/spark-warehouse/{app_name}.db/"
    if os.path.exists(config['app_data_path']):
        shutil.rmtree(config['app_data_path'])
    if os.path.exists(database_path):
        shutil.rmtree(database_path)

    spark.sql(f"DROP SCHEMA IF EXISTS {app_name} CASCADE ")


def load_config(config_path) :
    """Loads the configuration file"""
    with open(f"{config_path}", 'r') as f:
        config = json.load(f)
    return config


def start_staging_job(spark, config, task, timeout=None):
    """Creates the staging job"""
    schema = StructType.fromJson(task["schema"])
    location = task["location"]
    target = task["target"]
    type = task["type"]
    output = task["output"]
    format = task["format"]
    landing_path = config["landing_path"]
    staging_path = config["staging_path"]
    if type == "streaming":
        df = spark \
            .readStream \
            .format(format) \
            .option("multiline", "true") \
            .option("header", "true") \
            .schema(schema) \
            .load(landing_path+"/"+location)    

        if "table" in output:
            query = df.writeStream\
                .format(storage_format) \
                .outputMode("append")\
                .option("checkpointLocation", staging_path+"/"+target+"_chkpt")\
                .toTable(target)
            if timeout is not None:
                query.awaitTermination(timeout)
        if "file" in output:
            query = df.writeStream \
                .format(storage_format) \
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
            .load(landing_path+"/"+location)  

        if "table" in output:
            df.write.format(storage_format).mode("append").option("overwriteSchema", "true").saveAsTable(target)
        if "file" in output:
            df.write.format(storage_format).mode("append").option("overwriteSchema", "true").save(staging_path+"/"+target)
        if "view" in output:
            df.createOrReplaceTempView(target)
    else :
        raise Exception("Invalid type")
        

def start_standard_job(spark, config, task, timeout=None):
    """Creates the standard job"""
    standard_path = config["standard_path"]
    sql = task["sql"]
    output = task["output"]
    if(isinstance(sql, list)):
        sql = " \n".join(sql)
    target = task["target"]
    load_staging_views(spark, config)
    df = spark.sql(sql)
    type = "batch"
    if "type" in task:
        type = task["type"]
    if type == "streaming":
        if "table" in output:
            query = df.writeStream\
                .format(storage_format) \
                .outputMode("append")\
                .option("checkpointLocation", standard_path+"/"+target+"_chkpt")\
                .toTable(target)
            if timeout is not None:
                query.awaitTermination(timeout)
        if "file" in output:
            query = df.writeStream \
                .format(storage_format) \
                .outputMode("append") \
                .option("checkpointLocation", standard_path+"/"+target+"_chkpt") \
                .start(standard_path+"/"+target)
            if timeout is not None:
                query.awaitTermination(timeout)
        if "view" in output:
            df.createOrReplaceTempView(target)
    elif type == "batch":
        if "table" in output:
            df.write.format(storage_format).mode("append").option("overwriteSchema", "true").saveAsTable(target)
        if "file" in output:
            df.write.format(storage_format).mode("append").option("overwriteSchema", "true").save(standard_path+"/"+target)
        if "view" in output:
            df.createOrReplaceTempView(target)
    else :
        raise Exception("Invalid type")


def start_serving_job(spark, config, task, timeout=None):
    """Creates the serving job"""
    serving_path = config["serving_path"]
    sql = task["sql"]
    output = task["output"]
    if(isinstance(sql, list)):
        sql = " \n".join(sql)
    target = task["target"]
    type = "batch"
    if "type" in task:
        type = task["type"]
    load_staging_views(spark, config)
    load_standard_views(spark, config)
    df = spark.sql(sql)
    if type == "streaming":
        if "table" in output:
            query = df.writeStream\
                .format(storage_format) \
                .outputMode("complete")\
                .option("checkpointLocation", serving_path+"/"+target+"_chkpt")\
                .toTable(target)
            if timeout is not None:
                query.awaitTermination(timeout)
        if "file" in output:
            query = df.writeStream \
                .format(storage_format) \
                .outputMode("complete") \
                .option("checkpointLocation", serving_path+"/"+target+"_chkpt") \
                .start(serving_path+"/"+target)
            if timeout is not None:
                query.awaitTermination(timeout)
        if "view" in output:
            df.createOrReplaceTempView(target)

    elif type == "batch":
        if "table" in output:
            df.write.format(storage_format).mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)
        if "file" in output:
            df.write.format(storage_format).mode("overwrite").option("overwriteSchema", "true").save(serving_path+"/"+target)
        if "view" in output:
            df.createOrReplaceTempView(target)
    else :
        raise Exception("Invalid type")

def load_staging_views(spark, config):
    landing_path = config["landing_path"]
    if 'staging' in config:
        for task in config["staging"]:
            schema = StructType.fromJson(task["schema"])
            location = task["location"]
            target = task["target"]
            type = task["type"]
            output = task["output"]
            format = task["format"]
            if type == "streaming" and "view" in output:
                df = spark \
                    .readStream \
                    .format(format) \
                    .option("multiline", "true") \
                    .option("header", "true") \
                    .schema(schema) \
                    .load(landing_path+"/"+location)    
                df.createOrReplaceTempView(target)
            elif type == "batch" and "view" in output:
                df = spark \
                    .read \
                    .format(format) \
                    .option("multiline", "true") \
                    .option("header", "true") \
                    .schema(schema) \
                    .load(landing_path+"/"+location)  
                df.createOrReplaceTempView(target)


def load_standard_views(spark, config):
    if 'standard' in config:
        for task in config["standard"]:
            sql = task["sql"]
            output = task["output"]
            if(isinstance(sql, list)):
                sql = " \n".join(sql)
            target = task["target"]
            df = spark.sql(sql)
            if "view" in output:
                df.createOrReplaceTempView(target)

def show_serving_dataset(spark, config, task):
    """Shows the serving dataset"""
    serving_path = f"{config['working-dir']}/{config['name']}/serving"
    target = task["target"]
    df = spark.read.format(storage_format).load(serving_path+"/"+target)
    df.show()

def get_dataset_as_json(spark, config, stage, task, limit=20):
    """Shows the serving dataset"""
    staging_path = f"{config['working_dir']}/{config['name']}/staging"
    standard_path = f"{config['working_dir']}/{config['name']}/standard"
    serving_path = f"{config['working_dir']}/{config['name']}/serving"
    task_type = task["type"]
    task_output = task["output"]
    app_name = config["name"]
    spark.sql(f"USE SCHEMA {app_name}")
    if "view" in task_output and task_type != "streaming":
        target = task["target"]
        df = spark.sql("select * from "+target+" limit "+str(limit))
        return df.toJSON().map(lambda j: json.loads(j)).collect()
    elif "table" in task_output:
        target = task["target"]
        df = spark.sql("select * from "+target+" limit "+str(limit))
        return df.toJSON().map(lambda j: json.loads(j)).collect()
    elif "file" in task_output:
        target = task["target"]
        path = None
        if stage == "staging":
            path = staging_path            
        elif stage == "standard":
            path = standard_path
        elif stage == "serving":
            path = serving_path
        else:
            raise Exception("Invalid stage")
        df = spark.read.format(storage_format).load(path+"/"+target)
        df.createOrReplaceTempView("tmp_"+target)
        df = spark.sql("select * from tmp_"+target+ " limit "+str(limit))
        return df.toJSON().map(lambda j: json.loads(j)).collect()
    else:
        raise Exception("Invalid output")


def entrypoint():
    parser = argparse.ArgumentParser(description='Process Data pipeline')
    parser.add_argument('--config-path', help='path to pipeline config file', required=True)
    parser.add_argument('--landing-path', help='path to data landing zone', required=True)
    parser.add_argument('--working-dir', help='folder to store data of stages, the default value is a random tmp folder', required=False)
    parser.add_argument('--stage', help='run a task in the specified stage', required=False)
    parser.add_argument('--task', help='run a specified task', required=False)
    parser.add_argument('--show-result', type=bool, default=False, help='flag to show task data result', required=False)
    parser.add_argument('--await-termination', type=int, help='how many seconds to wait before streaming job terminating, no specified means not terminating.', required=False)

    args = parser.parse_args()

    config_path = args.config_path
    awaitTermination = args.await_termination
    stage_arg = args.stage
    task_arg = args.task
    working_dir = args.working_dir
    landing_path = args.landing_path
    show_result = args.show_result


    config = load_config(config_path)
    config['landing_path'] = landing_path
    config['working_dir'] = working_dir

    spark = create_spark_session()
   
    print(f"""app name: {config["name"]},
    config path: {config_path},
    landing path: {config['landing_path']},
    working dir:{config['working_dir']},
    stage: {stage_arg},
    task: {task_arg},   
    show_result: {show_result}, 
    streaming job waiting for {str(awaitTermination)} seconds before terminating
    """)

    init(spark, config, working_dir)
    init_database(spark, config)
    if 'staging' in config and (stage_arg is None or stage_arg == "staging"):
        for task in config["staging"]:
            if task_arg is None or task['name'] == task_arg:
                start_staging_job(spark, config, task, awaitTermination)
    if 'standard' in config and (stage_arg is None or stage_arg == "standard"):
        for task in config["standard"]:
            if task_arg is None or task['name'] == task_arg:
                start_standard_job(spark, config, task, awaitTermination)
    if 'serving' in config and (stage_arg is None or stage_arg == "serving"):
        for task in config["serving"]:
            if task_arg is None or task['name'] == task_arg:
                start_serving_job(spark, config, task, awaitTermination)
                if show_result:
                    print(get_dataset_as_json(spark, config, "serving", task))


def wait_for_next_stage():    
    parser = argparse.ArgumentParser(description='Wait for the next stage')
    parser.add_argument('--duration', type=int, default=10, help='how many seconds to wait', required=False)
    args = parser.parse_args()
    print(f"waiting for {args.duration} seconds to next stage")
    time.sleep(args.duration)

def load_sample_data(spark, data_str, format="json"):

    # save data_str to temp file
    temp_file = tempfile.NamedTemporaryFile(delete=False)
    temp_file.write(data_str.encode())
    temp_file.close()
    # print("temp file path: "+temp_file.name)
    file_path = temp_file.name
    # file_path = "./example/data/fruit-price/tmpasfs2n8k"
    if format == "json":
        # read json file to dataframe
        df = spark \
            .read \
            .format("json") \
            .option("header", "true") \
            .option("inferSchema", "true") \
            .load(temp_file.name)
    elif format == "csv":
        df = spark \
            .read \
            .format("csv") \
            .option("header", "true") \
            .option("inferSchema", "true") \
            .option("multiline", "true") \
            .load(file_path)
    # create random table name
    table_name = "tmp_"+str(uuid.uuid4()).replace("-", "")
    df.createOrReplaceTempView("tmp_"+table_name)
    df = spark.sql("select * from tmp_"+table_name+ " limit "+str(20))
    data = df.toJSON().map(lambda j: json.loads(j)).collect()
    json_str = json.dumps(data)
    schema = df.schema.json()
    return json_str, schema



