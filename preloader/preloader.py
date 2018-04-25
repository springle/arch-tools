""" Archer Preloader for Keystone Pipeline

The Keystone Pipeline pulls URLs from an exchange in RabbitMQ,
processes them, and outputs the results to a number of targets (Elasticsearch, Neo4j, etc).

This preloader populates that exchange in RabbitMQ,
so the Keystone Pipeline can start working.
"""

import os
import requests
import pika
import time

import google.cloud.storage as storage

sources_loaded = 0

class ArcherPreloader:

    def __init__(self, rmq_host, rmq_port, rmq_user,
                 rmq_pass, source_bucket, source_path,
                 target_exchange, data_format, url_limit):
        """
        :param rmq_host: (String) RabbitMQ hostname
        :param rmq_port: (Int) RabbitMQ port
        :param rmq_user: (String) RabbitMQ username
        :param rmq_pass: (String) RabbitMQ password
        :param source_bucket: (String) GCS bucket, usually archer-source-data
        :param source_path: (String) Path to folder in GCS bucket, contains sources to preload
        :param target_exchange: (String) RMQ exchange to dump URLs into
        :param data_format: (String) Data format of the files in source_bucket/source_path
        :param url_limit: (Int) Maximum number of URLs to pull
        """

        # Record params
        self.url_limit = url_limit
        self.target_exchange = target_exchange
        self.source_path = source_path

        # Pull sources from GCS
        bucket = storage.Client().get_bucket(source_bucket)
        self.sources = bucket.list_blobs(prefix=source_path)
        self.bucket_name = bucket.name

        # Initialize connection with RabbitMQ, declare exchange
        credentials = pika.PlainCredentials(rmq_user, rmq_pass)
        self.properties = pika.BasicProperties(headers={"dataFormat": data_format})
        self.connection = pika.BlockingConnection(pika.ConnectionParameters(host=rmq_host,
                                                                            port=rmq_port,
                                                                            credentials=credentials))
        self.channel = self.connection.channel()
        self.channel.exchange_declare(exchange=target_exchange, exchange_type="fanout")


    def preload(self):
        """
        Pull the URLs of every object in gs://SOURCE_BUCKET/SOURCE_PATH,
        and publish them to the TARGET_EXCHANGE.
        """
        global sources_loaded
        count = 0

        print("------------------")
        print("STARTING PRELOADER: %s/%s" % (self.bucket_name, self.source_path))
        print("------------------")

        for source in self.sources:

            # Respect url_limit
            if (sources_loaded >= self.url_limit) and (self.url_limit > 0):
                break

            # Ignore sources we have already loaded
            if count < sources_loaded or not count:
                count += 1
                continue

            url = "gs://%s/%s" % (self.bucket_name, source.name)
            self.channel.basic_publish(exchange=self.target_exchange,
                                       routing_key="",
                                       properties=self.properties,
                                       body=url)

            # Keep track of sources we have already loaded
            count += 1
            sources_loaded += 1

            # Periodically display progress
            if sources_loaded % 100 == 0:
                print("--> %s sources loaded" % sources_loaded)

        print("-------------------")
        print("FINISHING PRELOADER: %s sources loaded" % sources_loaded)
        print("-------------------")

        self.connection.close()


if __name__ == '__main__':

    # Pull out environment variables
    rmq_host = os.getenv("RMQ_HOST", "localhost")
    rmq_port = int(os.getenv("RMQ_PORT", 5672))
    rmq_user = os.getenv("RMQ_USER", "architect")
    rmq_pass = os.getenv("RMQ_PASS", "gotpublicdata")
    source_bucket = os.getenv("SOURCE_BUCKET", "archer-source-data")
    source_path = os.getenv("SOURCE_PATH", "usa/ofac")
    target_exchange = os.getenv("TARGET_EXCHANGE", "sources")
    data_format = os.getenv("DATA_FORMAT", "csv")
    url_limit = int(os.getenv("URL_LIMIT", -1))

    # Run preloader
    while True:
        time.sleep(1)
        try:
            preloader = ArcherPreloader(rmq_host, rmq_port, rmq_user,
                                        rmq_pass, source_bucket, source_path,
                                        target_exchange, data_format, url_limit)
            preloader.preload()
        except requests.exceptions.ConnectionError as e:
            print("disconnected from GCS -- reconnecting")
            continue
        except pika.exceptions.ConnectionClosed as e:
            print("disconnected from RabbitMQ -- reconnecting")
            continue
        break
