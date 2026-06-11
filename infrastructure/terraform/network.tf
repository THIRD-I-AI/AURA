# Network Segregation for Hybrid ERP Integration

# VPC specifically for the AURA Hybrid API Gateway
resource "aws_vpc" "aura_hybrid_vpc" {
  cidr_block           = "10.100.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "aura-hybrid-integration-vpc"
    Environment = var.environment
  }
}

# Private Subnets for On-Premise ERP Sync
resource "aws_subnet" "aura_private_subnets" {
  count             = 2
  vpc_id            = aws_vpc.aura_hybrid_vpc.id
  cidr_block        = cidrsubnet(aws_vpc.aura_hybrid_vpc.cidr_block, 8, count.index)
  availability_zone = element(var.availability_zones, count.index)

  tags = {
    Name = "aura-private-subnet-${count.index}"
    Network = "Segregated-ERP-Ingestion"
  }
}

# AWS Direct Connect / Transit Gateway Attachment (Mocked)
# This establishes the secure boundary to the legacy on-premise warehouses
resource "aws_ec2_transit_gateway_vpc_attachment" "on_prem_attachment" {
  subnet_ids         = aws_subnet.aura_private_subnets[*].id
  transit_gateway_id = var.enterprise_transit_gateway_id
  vpc_id             = aws_vpc.aura_hybrid_vpc.id
}
