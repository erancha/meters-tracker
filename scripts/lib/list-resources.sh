#!/usr/bin/env bash
# The --list-resources option of aws-helper.sh: the resources of a region that are not AWS account
# defaults (VPC networking, IAM, ECS containers, data stores, APIs, Lambda, CloudFormation stacks,
# CloudWatch dashboards and custom metrics), as one sorted, numbered list, e.g. to spot leftovers
# after deleting a stack. Sourced (never executed) after lib/app-env.sh.
#
# Core responsibilities:
# - list_resources <region>: prints the list; it only calls describe/list APIs
# - aws_jq / collect: one AWS CLI call rendered to lines by a jq filter, printed or appended to
#   the list

# shellcheck disable=SC2016  # jq filters and JMESPath literals are single-quoted on purpose

# items: AWS omits an empty list from a response, so a query over it yields null.
# pad: left-justifies a string to at least n characters.
# like: case-insensitive substring test.
LIST_RESOURCES_JQ_DEFS='
def items: if . == null then empty else .[] end;
def pad($n): if length < $n then . + (" " * ($n - length)) else . end;
def like($s): ascii_downcase | contains($s | ascii_downcase);
'

# Prints the lines a jq filter renders from an AWS CLI call's JSON output. Reads the region from
# the region variable of list_resources, its only caller chain.
aws_jq() {
    local filter="$1"
    shift
    aws "$@" --region "$region" --output json | jq -r "$LIST_RESOURCES_JQ_DEFS $filter"
}

# Appends the lines aws_jq renders to the resources array of list_resources.
collect() {
    local output lines
    output="$(aws_jq "$@")"
    split_lines lines "$output"
    resources+=("${lines[@]}")
}

list_resources() {
    local region="$1"
    local resources=()
    local named="Tags[?Key=='Name']"
    local name_tag="Tags[?Key=='Name'].Value | [0]"
    local text clusters services tasks cluster service
    local kms_aliases kms_keys key alias_name user_pools pool

    collect 'items | select(.Name != null and (.Name | like("!!! Default VPC !!!") | not))
            | "VPC:         \(.Name | pad(20)) : \(.ID | pad(25)) , CIDR: \(.CidrBlock)"' \
        ec2 describe-vpcs \
        --query "Vpcs[?$named].{ID:VpcId, Name:$name_tag, CidrBlock:CidrBlock}"

    collect 'items | select(.Name != null and (.Name | like("Default") | not))
            | "Subnet:      \(.Name | pad(20)) : \(.ID | pad(25)) , VPC ID: \(.VpcId)"
              + " , CIDR: \(.CidrBlock)"' \
        ec2 describe-subnets \
        --query "Subnets[?$named].{ID:SubnetId, Name:$name_tag, VpcId:VpcId, CidrBlock:CidrBlock}"

    collect 'items
            | "NAT Gateway:  \(.Name | pad(20)) : \(.ID) , VPC ID: \(.VpcId) , State: \(.State)"' \
        ec2 describe-nat-gateways \
        --query "NatGateways[?$named].{ID:NatGatewayId, Name:$name_tag, VpcId:VpcId, State:State}"

    collect 'items | items | select(.Name != null and .ID != null)
            | "NAT Instance: \(.Name | pad(25)) : \(.ID)"' \
        ec2 describe-instances \
        --query "Reservations[*].Instances[?Tags[?Key=='NAT' && Value=='true']]
            .{ID:InstanceId,Name:$name_tag}"

    collect 'items | select(.Name != null and (.Name | like("!!! Default RTB !!!") | not))
            | "Route Table: \(.Name | pad(20)) : \(.ID | pad(25)) , VPC ID: \(.VpcId)"' \
        ec2 describe-route-tables \
        --query "RouteTables[?$named].{ID:RouteTableId, Name:$name_tag, VpcId:VpcId}"

    collect 'items | "Elastic IP: \(.IP) - Allocation ID: \(.AllocationId)"' \
        ec2 describe-addresses --query "Addresses[].{IP:PublicIp, AllocationId:AllocationId}"

    collect 'items | select(.Type == "Interface" or .Type == "Gateway")
            | "VPC Endpoint \("(\(.Type))" | pad(11)) : \(.ID) , Service Name: \(.ServiceName)"' \
        ec2 describe-vpc-endpoints \
        --query "VpcEndpoints[].{ID:VpcEndpointId, ServiceName:ServiceName, Type:VpcEndpointType}"

    # A detached internet gateway has no VPC.
    collect 'items | select(.Name != null and (.Name | like("!!! Default IGW !!!") | not))
            | "Internet Gateway: \(.Name | pad(10)) (\(.ID)) , VPC ID: \(.VpcId // "")"' \
        ec2 describe-internet-gateways \
        --query "InternetGateways[?$named]
            .{ID:InternetGatewayId, VpcId:Attachments[0].VpcId, Name:$name_tag}"

    collect 'items | "Security Group: \(.Name | pad(40)) : \(.ID)"' \
        ec2 describe-security-groups \
        --query "SecurityGroups[?GroupName!='default'].{ID:GroupId,Name:GroupName}"

    collect 'items | "Network ACL: \(.)"' \
        ec2 describe-network-acls --query 'NetworkAcls[?IsDefault==`false`].NetworkAclId'

    collect 'items | "IAM Policy: \(.Name | pad(20)) : \(.ID)"' \
        iam list-policies --scope Local --query "Policies[].{ID:PolicyId,Name:PolicyName}"

    collect 'items | select(.Name | startswith("AWS") or startswith("ec2-") | not)
            | "IAM Role: \(.Name | pad(65)) : \(.ID)"' \
        iam list-roles --query "Roles[?RoleName!='default'].{Name:RoleName,ID:RoleId}"

    collect 'items | "EventBridge Bus: \(.Name | pad(20)) : \(.Arn)"' \
        events list-event-buses --query "EventBuses[?Name!='default'].{Name:Name,Arn:Arn}"

    collect 'items | "Load Balancer: \(.Name | pad(20)) : \(.ID)"' \
        elbv2 describe-load-balancers \
        --query "LoadBalancers[].{ID:LoadBalancerArn,Name:LoadBalancerName}"

    text="$(aws_jq items ecs list-clusters --query clusterArns)"
    split_lines clusters "$text"
    for cluster in "${clusters[@]}"; do
        text="$(aws_jq items ecs list-services --cluster "$cluster" --query serviceArns)"
        split_lines services "$text"
        for service in "${services[@]}"; do
            text="$(aws_jq items ecs list-tasks --cluster "$cluster" --service-name "$service" \
                --query taskArns)"
            split_lines tasks "$text"
            if [[ ${#tasks[@]} -gt 0 ]]; then
                collect ".tasks[] | (.taskArn | split(\"/\")[-1]) as \$task | .containers[]
                    | \"ECS Container: Cluster=${cluster##*:}, Service=${service##*:},\"
                      + \" Task=\(\$task), Name=\(.name), Status=\(.lastStatus)\"" \
                    ecs describe-tasks --cluster "$cluster" --tasks "${tasks[@]}"
            fi
        done
    done

    collect 'items | (.Name // "<No Name>") as $name | select($name != "Default")
            | "EC2 Instance: \($name | pad(25)) : \(.ID)"' \
        ec2 describe-instances \
        --query "Reservations[*].Instances[].{ID:InstanceId,Name:$name_tag}"

    collect 'items | "RDS Instance: \(.)"' \
        rds describe-db-instances \
        --query "DBInstances[?DBInstanceIdentifier!='default'].DBInstanceIdentifier"

    collect 'items | "DynamoDB Table: \(.)"' \
        dynamodb list-tables --query "TableNames[?!contains(@, 'default')]"

    collect 'items | "Secret: \(.Name | pad(25)) : \(.ID)"' \
        secretsmanager list-secrets --query "SecretList[?Name!='default'].{ID: ARN, Name: Name}"

    kms_aliases="$(aws kms list-aliases --region "$region" --query Aliases --output json)"
    text="$(aws_jq items kms list-keys --query 'Keys[].KeyId')"
    split_lines kms_keys "$text"
    for key in "${kms_keys[@]}"; do
        alias_name="$(jq -r --arg key "$key" \
            '[.[] | select(.TargetKeyId == $key) | .AliasName] | last // "No Alias"' \
            <<<"$kms_aliases")"
        collect "select(.KeyState == \"Enabled\" and .KeyManager == \"CUSTOMER\")
            | \"KMS Key: $alias_name : \(.KeyId) \(.CreationDate)\"" \
            kms describe-key --key-id "$key" --query KeyMetadata
    done

    collect 'items | "SQS Queue: \(.)"' \
        sqs list-queues --query "QueueUrls[?!contains(@, 'default')]"

    collect 'items | "ElastiCache Cluster: \(.)"' \
        elasticache describe-cache-clusters \
        --query "CacheClusters[?CacheClusterStatus=='available'].CacheClusterId"

    collect 'items | "REST API Gateway     : \(.Name | pad(20)) : \(.ID)"' \
        apigateway get-rest-apis --query "items[?name!='default'].{ID:id,Name:name}"
    collect 'items | "WebSocket API Gateway: \(.Name | pad(20)) : \(.ID)"' \
        apigatewayv2 get-apis --query "Items[?Name!='default'].{ID:ApiId,Name:Name}"

    text="$(aws_jq items cognito-idp list-user-pools --max-results 60 \
        --query "UserPools[?Name!='default'].Id")"
    split_lines user_pools "$text"
    # A user pool without a hosted UI has no domain.
    for pool in "${user_pools[@]}"; do
        collect "\"Cognito User Pool    : \(.Name | pad(20)) : \(.Id) ,\"
            + \" domain: https://\(.Domain // \"\").auth.$region.amazoncognito.com\"" \
            cognito-idp describe-user-pool --user-pool-id "$pool" --query UserPool
    done
    collect 'items | "Cognito Identity Pool: \(.Name | pad(20)) : \(.ID)"' \
        cognito-identity list-identity-pools --max-results 60 \
        --query "IdentityPools[?IdentityPoolName!='default']
            .{ID:IdentityPoolId,Name:IdentityPoolName}"

    collect 'items
            | select(. != "default")
            | select(ascii_downcase | startswith("aws-sam-cli-managed-default") | not)
            | "S3 Bucket: \(.)"' \
        s3api list-buckets --query "Buckets[].Name"

    collect 'items | .ID as $id
            | .Origins[] | "CloudFront Distribution, origins: \(pad(60)) , \($id)"' \
        cloudfront list-distributions \
        --query "DistributionList.Items[?Id!='default'].{ID:Id, Origins:Origins.Items[].DomainName}"

    collect 'items | "Lambda Function: \(.)"' \
        lambda list-functions --query "Functions[?FunctionName!='default'].FunctionName"
    collect 'items | "Lambda Layer: \(.)"' \
        lambda list-layers --query "Layers[?LayerName!='default'].LayerName"

    collect "items | \"CloudFormation Stack '\(.)'\"" \
        cloudformation list-stacks \
        --query "StackSummaries[?StackStatus!='DELETE_COMPLETE'].StackName"

    collect 'items | "CloudWatch Dashboard: \(.)"' \
        cloudwatch list-dashboards \
        --query "DashboardEntries[?DashboardName!='default'].DashboardName"

    warn "$(date +%H:%M:%S) : Retrieving CloudWatch custom metrics..."
    # Custom metrics are those outside the AWS/ namespaces; one line per metric name, counting its
    # dimension combinations.
    collect 'map(select(.Namespace | startswith("AWS/") | not))
            | if length == 0 then
                "CloudWatch Custom Metric: No custom metrics found"
                + " (only AWS service metrics detected)"
              else
                group_by(.Namespace, .MetricName)[]
                | "CloudWatch Custom Metric: Namespace=\(.[0].Namespace)"
                  + " | Metric=\(.[0].MetricName) | Count=\(length)"
                  + (.[0].Dimensions | if length > 0
                      then " | Sample dimensions: " + (map("\(.Name)=\(.Value)") | join(", "))
                      else "" end)
              end' \
        cloudwatch list-metrics --query Metrics

    # Case-insensitive order, numbered from 1.
    printf '%s\n' "${resources[@]}" | LC_ALL=C sort -f | awk '{ printf "#%3d : %s\n", NR, $0 }'
}
