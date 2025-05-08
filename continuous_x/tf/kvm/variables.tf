variable "key" {
  description = "Name of key pair"
  type        = string
  default     = "id_rsa_chameleon"
}

variable "nodes" {
  type = map(string)
  default = {
    "node1" = "192.168.32.11"  # Changed from 192.168.1.11 to match the CIDR
    "node2" = "192.168.32.12"  # Changed from 192.168.1.12 to match the CIDR
    "node3" = "192.168.32.13"  # Changed from 192.168.1.13 to match the CIDR
  }
}