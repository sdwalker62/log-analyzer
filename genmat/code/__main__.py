import numpy as np
import pandas as pd
import math as math
import os
import joblib
import time
from drain3.drain import Drain
from datetime import timedelta
from multiprocessing import Process, Manager
from genmat.code.database_methods import create_connection


def genmat(cd, idx, di, labels, considered_container_names, tf, dd):
    ret = {label: {} for label in labels}
    for label in labels:
        dl = di[di['label'] == label]
        start_time = dl['timestamp'].iloc[0]
        end_time = dl['timestamp'].iloc[-1]
        n_intervals = math.ceil((end_time - start_time) / tf)
        for container_name in considered_container_names:
            logs = di[(di['label'] == label) & (di['container_name'] == container_name)]
            drain_model = dd[container_name]
            counter_array = np.zeros((len(drain_model.clusters), n_intervals))
            for log in logs.itertuples():
                time_stamp = log.timestamp
                # instead of iterating over each time period we can calculate which period the log arrived
                col = math.floor((time_stamp - start_time) / tf)
                match_cluster, update_type = drain_model.add_log_message(log.log)
                counter_array[match_cluster.cluster_id - 1, col] += 1
            ret[label][container_name] = counter_array

    cd[idx] = ret


def generate_drain_models(dd, df, container):
    drain_model = Drain()
    for log in df.itertuples():
        drain_model.add_log_message(log.log)
    dd[container] = drain_model


def cluster_visualizer(df: pd.DataFrame, tf: timedelta):
    """
    Each cluster will
    :param
        df: pd.DataFrame
            Pandas dataframe where each row is an example with time stamp, container name, log, and label as columns
        tf: datetime.timedelta
            Time frame for clustering arrivals

    :return
        ret_dict: dict
            Dictionary of integers representing the number of observations for each
            (label, container, cluster) coordinate of the arrivals dictionary falling
            within the timeframe determined by time_frame.
    """

    fmt = '%Y-%m-%dT%H:%M:%S.%f'
    df['timestamp'] = pd.to_datetime(df['timestamp'], format=fmt)
    labels = set(df['label'].unique().tolist())

    all_container_names = set(df['container_name'].unique().tolist())
    forbidden_containers = {'solr'}
    considered_container_names = all_container_names - forbidden_containers

    # pd.DataFrame with True in the index where a label has changed and false otherwise
    transitions = df['label'] != df['label'].shift(axis=0)

    # numpy.ndarray of indices where the transition occurred, i.e. where the True values are in the
    # transitions dataframe
    transitions_indices = np.where(transitions)[0]

    # determine where each collection begins
    ce = list(transitions_indices[::len(labels)])
    ce.append(len(df.index))
    n_collections = len(ce)

    # the goal here is to make a drain model for each container that has clusters for all observations
    # from all collections so that we may see which receive messages during specific collection runs

    print("Building drain models...")
    ldict = {container: df[df['container_name'] == container] for container in considered_container_names}
    # dd = {container_name: Drain() for container_name in considered_container_names}
    drain_manager = Manager()
    dd = drain_manager.dict()
    drain_job = [Process(target=generate_drain_models,
                         args=(dd, ldict[container], container))
                 for container in considered_container_names]
    _ = [process.start() for process in drain_job]
    _ = [process.join() for process in drain_job]
    # for log in df.itertuples():
    #    if log.container_name not in forbidden_containers:
    #        dd[log.container_name].add_log_message(log.log)

    # now that we have these drain models we can analyze each collection:

    # split the dataframe into sub-dataframes by collection
    dfs = {idx: df.iloc[ce[idx] + 1:ce[idx + 1] + 1, :].sort_values(by='timestamp') for idx in range(n_collections - 2)}

    # build the cluster count dictionary

    # for idx in dfs.keys():

    manager = Manager()
    cd = manager.dict()
    #      for idx in dfs.keys()}
    print("beginning parallel section")
    job = [Process(target=genmat, args=(cd, i, dfs[i], labels, considered_container_names, tf, dd)) for i in dfs.keys()]
    _ = [process.start() for process in job]
    _ = [process.join() for process in job]

    return labels, considered_container_names, dfs.keys(), cd, dd


def main():
    db_path = '/code/database/elastic_logs.db'
    db_conn = create_connection(db_path)

    # make dataframe and sort by timestamp
    sql_query = 'SELECT * FROM logs'
    df = pd.read_sql_query(sql_query, db_conn)
    tf = timedelta(seconds=int(os.getenv("TIME_FRAME")))

    print("Starting matrix creation...")
    labels, containers, collections, cd, dd = cluster_visualizer(df, tf)
    print("... matrix creation complete!")

    print("Saving results...")
    joblib.dump(labels, "/code/results/labels.joblib")
    joblib.dump(containers, "/code/results/containers.joblib")
    joblib.dump(set(collections), "/code/results/collections.joblib")
    joblib.dump(dict(cd), "/code/results/matrices_dict.joblib")
    joblib.dump(dict(dd), "/code/results/drain_dict.joblib")
    print("... saving complete!")


if __name__ == '__main__':
    start = time.time()
    main()
    print(f"Runtime: {time.time()-start}")
