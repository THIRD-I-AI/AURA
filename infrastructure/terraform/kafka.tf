# Amazon MSK (Managed Streaming for Apache Kafka) Cluster

resource "aws_msk_cluster" "aura_enterprise_kafka" {
  cluster_name           = "aura-ledger-event-bus"
  kafka_version          = "3.5.1"
  number_of_broker_nodes = 3

  broker_node_group_info {
    instance_type  = "kafka.m5.large"
    client_subnets = aws_subnet.aura_private_subnets[*].id
    security_groups = [aws_security_group.kafka_sg.id]
    
    storage_info {
      ebs_storage_info {
        volume_size = 1000 # 1TB per broker for massive ledger volume
      }
    }
  }

  encryption_info {
    encryption_in_transit {
      client_broker = "TLS"
      in_cluster    = true
    }
  }

  tags = {
    Environment = var.environment
    Component   = "Event-Bus"
  }
}

# In an enterprise setup, you would manage topics explicitly via a Kafka Provider,
# but we document the exact DLQ topic definitions required for AURA here.
/*
resource "kafka_topic" "aura_ledger_ingested" {
  name               = "aura.ledger.ingested"
  replication_factor = 3
  partitions         = 30 # High partition count to support parallel node synchronization
}

resource "kafka_topic" "aura_dlq_ledger" {
  name               = "aura.dlq.ledger"
  replication_factor = 3
  partitions         = 10
}
*/
