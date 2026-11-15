(define (problem rebuild-01)
  (:domain blocks-world)
  (:objects a b c d e - block)
  (:init
    (clear a) (clear b) (clear c) (clear e) (hand-empty) (on c d) (on-table a) (on-table b) (on-table d) (on-table e))
  (:goal (and (on a d) (on c e) (on d c) (on e b) (on-table b)))
)
